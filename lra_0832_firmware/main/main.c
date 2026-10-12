#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/*
 * Separate DRV2605L firmware for a two-wire 0832 LRA.
 *
 * Default actuator profile: 235 Hz resonance, 2.0 V RMS rated voltage,
 * 2.4 V peak overdrive clamp. BOOT tries this profile first, then tries the
 * 205 Hz 0832 profile if auto-calibration fails.
 */
#define LRA_DEFAULT_RESONANCE_HZ 235

#if LRA_DEFAULT_RESONANCE_HZ != 235 && LRA_DEFAULT_RESONANCE_HZ != 205
#error "Supported 0832 profiles are 205 Hz and 235 Hz"
#endif

#define LRA_235_DRIVE_TIME_REG      0x10
#define LRA_235_RATED_VOLTAGE_REG   0x4E
#define LRA_205_DRIVE_TIME_REG      0x13
#define LRA_205_RATED_VOLTAGE_REG   0x51

#if LRA_DEFAULT_RESONANCE_HZ == 235
#define LRA_FALLBACK_RESONANCE_HZ   205
#define LRA_DEFAULT_DRIVE_TIME_REG  LRA_235_DRIVE_TIME_REG
#define LRA_DEFAULT_RATED_VOLTAGE   LRA_235_RATED_VOLTAGE_REG
#define LRA_FALLBACK_DRIVE_TIME_REG LRA_205_DRIVE_TIME_REG
#define LRA_FALLBACK_RATED_VOLTAGE  LRA_205_RATED_VOLTAGE_REG
#else
#define LRA_FALLBACK_RESONANCE_HZ   235
#define LRA_DEFAULT_DRIVE_TIME_REG  LRA_205_DRIVE_TIME_REG
#define LRA_DEFAULT_RATED_VOLTAGE   LRA_205_RATED_VOLTAGE_REG
#define LRA_FALLBACK_DRIVE_TIME_REG LRA_235_DRIVE_TIME_REG
#define LRA_FALLBACK_RATED_VOLTAGE  LRA_235_RATED_VOLTAGE_REG
#endif

#define LRA_OVERDRIVE_CLAMP_REG 0x71

#define I2C_PORT          I2C_NUM_0
/* ESP32-S3-Touch-LCD-1.46 J9 header wiring: SCL = GPIO10 (J9 pin 6),
   SDA = GPIO11 (J9 pin 8), shared with the touch/IMU/RTC bus. */
#define I2C_SDA_GPIO      11
#define I2C_SCL_GPIO      10
#define I2C_FREQ_HZ       400000
#define BOOT_GPIO         GPIO_NUM_0
#define SERIAL_BAUD       115200
#define SERIAL_TIMEOUT_MS 500
#define DRV2605L_ADDR     0x5A

#define REG_STATUS        0x00
#define REG_MODE          0x01
#define REG_RTP_INPUT     0x02
#define REG_GO            0x0C
#define REG_RATED_VOLTAGE 0x16
#define REG_OD_CLAMP      0x17
#define REG_A_CAL_COMP    0x18
#define REG_A_CAL_BEMF    0x19
#define REG_FEEDBACK      0x1A
#define REG_CONTROL1      0x1B
#define REG_CONTROL2      0x1C
#define REG_CONTROL3      0x1D
#define REG_CONTROL4      0x1E
#define REG_CONTROL5      0x1F
#define REG_LRA_PERIOD    0x22

#define MODE_STANDBY      0x40
#define MODE_RTP          0x05
#define MODE_AUTOCAL      0x07

#define STATUS_DIAG_FAIL  0x08

/* LRA, 3x braking, high loop gain, default 15x initial BEMF gain. */
#define LRA_FEEDBACK_AUTOCAL 0xAA
#define LRA_CONTROL1_STARTUP_BOOST 0x80
/* Bidirectional during calibration, brake stabilization, sample time 300 us,
   blanking and current-dissipation values of 1. */
#define LRA_CONTROL2_AUTOCAL 0xF5
/* Unsigned RTP and twice-per-cycle amplitude updates; closed-loop LRA. */
#define LRA_CONTROL3_RTP     0x0C
/* 1.0-1.2 second auto-calibration window, 100 us zero-crossing detect. */
#define LRA_CONTROL4_AUTOCAL 0x30
#define LRA_CONTROL5_AUTOCAL 0x80
/* Closed-loop unidirectional playback with automatic braking. */
#define LRA_CONTROL2_RTP     0x75

#define CALIBRATION_TIMEOUT_MS 1800
#define BOOT_TEST_LEVEL         88
#define BOOT_TEST_MS            180

static const char *TAG = "lra0832";

typedef struct {
    uint16_t resonance_hz;
    uint8_t drive_time;
    uint8_t rated_voltage;
} lra_profile_t;

static const lra_profile_t lra_profiles[] = {
    {
        .resonance_hz = LRA_DEFAULT_RESONANCE_HZ,
        .drive_time = LRA_DEFAULT_DRIVE_TIME_REG,
        .rated_voltage = LRA_DEFAULT_RATED_VOLTAGE,
    },
    {
        .resonance_hz = LRA_FALLBACK_RESONANCE_HZ,
        .drive_time = LRA_FALLBACK_DRIVE_TIME_REG,
        .rated_voltage = LRA_FALLBACK_RATED_VOLTAGE,
    },
};

static esp_err_t drv_write(uint8_t reg, uint8_t value)
{
    uint8_t data[2] = {reg, value};
    return i2c_master_write_to_device(
        I2C_PORT,
        DRV2605L_ADDR,
        data,
        sizeof(data),
        pdMS_TO_TICKS(100));
}

static esp_err_t drv_read(uint8_t reg, uint8_t *value)
{
    return i2c_master_write_read_device(
        I2C_PORT,
        DRV2605L_ADDR,
        &reg,
        1,
        value,
        1,
        pdMS_TO_TICKS(100));
}

static esp_err_t i2c_init(void)
{
    i2c_config_t config = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_SDA_GPIO,
        .scl_io_num = I2C_SCL_GPIO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_FREQ_HZ,
    };
    ESP_ERROR_CHECK(i2c_param_config(I2C_PORT, &config));
    return i2c_driver_install(I2C_PORT, config.mode, 0, 0, 0);
}

static esp_err_t serial_init(void)
{
    uart_config_t config = {
        .baud_rate = SERIAL_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };

    uart_driver_delete(UART_NUM_0);
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &config));
    ESP_ERROR_CHECK(uart_set_pin(
        UART_NUM_0,
        UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE));
    return uart_driver_install(UART_NUM_0, 2048, 0, 0, NULL, 0);
}

static void drv_stop(void)
{
    ESP_ERROR_CHECK(drv_write(REG_RTP_INPUT, 0));
    ESP_ERROR_CHECK(drv_write(REG_MODE, MODE_STANDBY));
}

static uint8_t protocol_to_unsigned_rtp(uint8_t level)
{
    if (level == 0) {
        return 0;
    }
    return (uint8_t)(((uint16_t)level * 255U + 63U) / 127U);
}

static void drv_set_level(uint8_t level)
{
    ESP_ERROR_CHECK(drv_write(REG_MODE, MODE_RTP));
    ESP_ERROR_CHECK(
        drv_write(REG_RTP_INPUT, protocol_to_unsigned_rtp(level)));
}

static void drv_prepare_uncalibrated_rtp(const lra_profile_t *profile)
{
    ESP_ERROR_CHECK(drv_write(REG_RATED_VOLTAGE, profile->rated_voltage));
    ESP_ERROR_CHECK(drv_write(REG_OD_CLAMP, LRA_OVERDRIVE_CLAMP_REG));
    ESP_ERROR_CHECK(drv_write(REG_FEEDBACK, LRA_FEEDBACK_AUTOCAL));
    ESP_ERROR_CHECK(drv_write(
        REG_CONTROL1,
        LRA_CONTROL1_STARTUP_BOOST | profile->drive_time));
    ESP_ERROR_CHECK(drv_write(REG_CONTROL2, LRA_CONTROL2_RTP));
    ESP_ERROR_CHECK(drv_write(REG_CONTROL3, LRA_CONTROL3_RTP));
    ESP_ERROR_CHECK(drv_write(REG_MODE, MODE_RTP));
    ESP_ERROR_CHECK(drv_write(REG_RTP_INPUT, 0));
}

static void boot_confirmation_pulse(const lra_profile_t *profile)
{
    ESP_LOGI(TAG, "BOOT short vibration check");
    drv_prepare_uncalibrated_rtp(profile);
    drv_set_level(BOOT_TEST_LEVEL);
    vTaskDelay(pdMS_TO_TICKS(BOOT_TEST_MS));
    drv_set_level(0);
    drv_stop();
}

static bool lra_auto_calibrate(const lra_profile_t *profile)
{
    ESP_LOGI(
        TAG,
        "calibrating %d Hz 0832 LRA (rated 2.0 V RMS, clamp 2.4 V peak)",
        profile->resonance_hz);

    drv_prepare_uncalibrated_rtp(profile);
    ESP_ERROR_CHECK(drv_write(REG_MODE, MODE_AUTOCAL));
    ESP_ERROR_CHECK(drv_write(REG_CONTROL2, LRA_CONTROL2_AUTOCAL));
    ESP_ERROR_CHECK(drv_write(REG_CONTROL4, LRA_CONTROL4_AUTOCAL));
    ESP_ERROR_CHECK(drv_write(REG_CONTROL5, LRA_CONTROL5_AUTOCAL));
    ESP_ERROR_CHECK(drv_write(REG_GO, 0x01));

    int elapsed_ms = 0;
    uint8_t go = 1;
    while (elapsed_ms < CALIBRATION_TIMEOUT_MS) {
        vTaskDelay(pdMS_TO_TICKS(10));
        elapsed_ms += 10;
        ESP_ERROR_CHECK(drv_read(REG_GO, &go));
        if ((go & 0x01) == 0) {
            break;
        }
    }
    if ((go & 0x01) != 0) {
        ESP_LOGE(
            TAG,
            "LRA auto-calibration timed out at %d Hz",
            profile->resonance_hz);
        drv_stop();
        return false;
    }

    uint8_t status = 0;
    uint8_t compensation = 0;
    uint8_t bemf = 0;
    uint8_t feedback = 0;
    uint8_t period = 0;
    ESP_ERROR_CHECK(drv_read(REG_STATUS, &status));
    ESP_ERROR_CHECK(drv_read(REG_A_CAL_COMP, &compensation));
    ESP_ERROR_CHECK(drv_read(REG_A_CAL_BEMF, &bemf));
    ESP_ERROR_CHECK(drv_read(REG_FEEDBACK, &feedback));
    ESP_ERROR_CHECK(drv_read(REG_LRA_PERIOD, &period));
    if ((status & STATUS_DIAG_FAIL) != 0) {
        ESP_LOGE(
            TAG,
            "LRA auto-calibration failed at %d Hz: status=0x%02X COMP=0x%02X BEMF=0x%02X FB=0x%02X period=0x%02X",
            profile->resonance_hz,
            status,
            compensation,
            bemf,
            feedback,
            period);
        drv_stop();
        return false;
    }

    ESP_ERROR_CHECK(drv_write(REG_CONTROL2, LRA_CONTROL2_RTP));
    ESP_ERROR_CHECK(drv_write(REG_CONTROL3, LRA_CONTROL3_RTP));
    ESP_ERROR_CHECK(drv_write(REG_MODE, MODE_RTP));
    ESP_ERROR_CHECK(drv_write(REG_RTP_INPUT, 0));
    ESP_LOGI(
        TAG,
        "calibration passed at %d Hz: COMP=0x%02X BEMF=0x%02X FB=0x%02X period=0x%02X",
        profile->resonance_hz,
        compensation,
        bemf,
        feedback,
        period);
    return true;
}

static bool boot_enable_lra(void)
{
    for (size_t i = 0; i < sizeof(lra_profiles) / sizeof(lra_profiles[0]); ++i) {
        if (lra_auto_calibrate(&lra_profiles[i])) {
            boot_confirmation_pulse(&lra_profiles[i]);
            return true;
        }

        if (i + 1 < sizeof(lra_profiles) / sizeof(lra_profiles[0])) {
            ESP_LOGW(
                TAG,
                "retrying BOOT calibration with %d Hz fallback profile",
                lra_profiles[i + 1].resonance_hz);
        }
    }

    /* Keep calibration failure visible, but let the same RTP path drive audio
       if the BOOT pulse proves the actuator can physically move. */
    const lra_profile_t *fallback_profile =
        &lra_profiles[(sizeof(lra_profiles) / sizeof(lra_profiles[0])) - 1];
    boot_confirmation_pulse(fallback_profile);
    drv_prepare_uncalibrated_rtp(fallback_profile);
    ESP_LOGW(
        TAG,
        "calibration failed; accepting H0..H127 in uncalibrated %d Hz RTP mode",
        fallback_profile->resonance_hz);
    return true;
}

static bool serial_read_level(uint8_t *requested_level)
{
    static char line[16];
    static size_t length = 0;
    bool received_level = false;
    uint8_t c;

    while (uart_read_bytes(UART_NUM_0, &c, 1, 0) == 1) {
        if (c == '\n' || c == '\r') {
            line[length] = '\0';
            if (length > 1 && line[0] == 'H') {
                int level = atoi(&line[1]);
                if (level < 0) {
                    level = 0;
                }
                if (level > 127) {
                    level = 127;
                }
                *requested_level = (uint8_t)level;
                received_level = true;
            }
            length = 0;
        } else if (length < sizeof(line) - 1) {
            line[length++] = (char)c;
        }
    }
    return received_level;
}

void app_main(void)
{
    ESP_ERROR_CHECK(i2c_init());
    drv_stop();
    ESP_ERROR_CHECK(serial_init());

    gpio_config_t boot_button = {
        .pin_bit_mask = 1ULL << BOOT_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&boot_button));

    ESP_LOGI(
        TAG,
        "reset-safe stop: 0832 LRA disabled; press BOOT to calibrate and enable");
    bool motor_enabled = false;
    bool serial_active = false;
    bool disabled_command_logged = false;
    uint8_t current_level = 0;
    unsigned long command_count = 0;
    TickType_t last_serial_command = xTaskGetTickCount();
    TickType_t last_status = xTaskGetTickCount();

    while (1) {
        if (gpio_get_level(BOOT_GPIO) == 0) {
            motor_enabled = boot_enable_lra();
            serial_active = false;
            while (gpio_get_level(BOOT_GPIO) == 0) {
                vTaskDelay(pdMS_TO_TICKS(20));
            }
            if (motor_enabled) {
                ESP_LOGI(TAG, "0832 LRA enabled: accepting H0..H127");
            }
        }

        uint8_t requested_level = 0;
        if (serial_read_level(&requested_level)) {
            if (motor_enabled) {
                drv_set_level(requested_level);
                current_level = requested_level;
                command_count++;
                last_serial_command = xTaskGetTickCount();
                serial_active = true;
            } else {
                serial_active = false;
                if (requested_level > 0 && !disabled_command_logged) {
                    ESP_LOGW(
                        TAG,
                        "ignored H%u: LRA remains reset-latched off",
                        requested_level);
                    disabled_command_logged = true;
                }
            }
        }

        if (serial_active
            && (xTaskGetTickCount() - last_serial_command)
                >= pdMS_TO_TICKS(SERIAL_TIMEOUT_MS)) {
            drv_set_level(0);
            current_level = 0;
            serial_active = false;
            ESP_LOGW(TAG, "serial timeout: motor stopped");
        }

        if ((xTaskGetTickCount() - last_status) >= pdMS_TO_TICKS(1000)) {
            ESP_LOGI(
                TAG,
                "heartbeat: enabled=%s level=%u commands=%lu link=%s",
                motor_enabled ? "yes" : "no",
                current_level,
                command_count,
                serial_active ? "active" : "idle");
            last_status = xTaskGetTickCount();
        }

        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
