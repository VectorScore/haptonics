#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>

#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/*
 * Two DRV2605L boards are required because their fixed 0x5A address cannot
 * coexist on one I2C bus.
 *
 * H channel: 0832 LRA on GPIO21 SDA / GPIO22 SCL.
 * L channel: JYLRA1030Z on GPIO32 SDA / GPIO33 SCL.
 */
#define LRA0832_RESONANCE_HZ 235

#if LRA0832_RESONANCE_HZ == 235
#define LRA0832_DRIVE_TIME 0x10
#define LRA0832_RATED_VOLTAGE 0x4E
#elif LRA0832_RESONANCE_HZ == 205
#define LRA0832_DRIVE_TIME 0x13
#define LRA0832_RATED_VOLTAGE 0x51
#else
#error "Supported 0832 profiles are 205 Hz and 235 Hz"
#endif

#define I2C_FREQ_HZ 400000
#define BOOT_GPIO GPIO_NUM_0
#define SERIAL_BAUD 115200
#define SERIAL_TIMEOUT_MS 500
#define DRV2605L_ADDR 0x5A

#define REG_STATUS 0x00
#define REG_MODE 0x01
#define REG_RTP_INPUT 0x02
#define REG_GO 0x0C
#define REG_RATED_VOLTAGE 0x16
#define REG_OD_CLAMP 0x17
#define REG_AUTOCAL_COMP 0x18
#define REG_AUTOCAL_BEMF 0x19
#define REG_FEEDBACK 0x1A
#define REG_CONTROL1 0x1B
#define REG_CONTROL2 0x1C
#define REG_CONTROL3 0x1D
#define REG_CONTROL4 0x1E
#define REG_CONTROL5 0x1F
#define REG_OL_LRA_PERIOD 0x20
#define REG_LRA_PERIOD 0x22

#define MODE_STANDBY 0x40
#define MODE_RTP 0x05
#define MODE_AUTOCAL 0x07
#define STATUS_DIAG_FAIL 0x08

typedef struct {
    i2c_port_t port;
    gpio_num_t sda;
    gpio_num_t scl;
    const char *name;
    uint8_t rated_voltage;
    uint8_t overdrive_clamp;
    uint8_t feedback;
    uint8_t control1;
    uint8_t control2_cal;
    uint8_t control2_rtp;
    uint8_t control3_rtp;
    uint8_t control4;
    uint8_t control5;
    bool unsigned_rtp;
    bool allow_210hz_fallback;
} lra_driver_t;

static const char *TAG = "dual_lra";

static const lra_driver_t lra0832 = {
    .port = I2C_NUM_0,
    .sda = GPIO_NUM_21,
    .scl = GPIO_NUM_22,
    .name = "0832",
    .rated_voltage = LRA0832_RATED_VOLTAGE,
    .overdrive_clamp = 0x71,
    .feedback = 0xAA,
    .control1 = 0x80 | LRA0832_DRIVE_TIME,
    .control2_cal = 0xF5,
    .control2_rtp = 0x75,
    .control3_rtp = 0x0C,
    .control4 = 0x30,
    .control5 = 0x80,
    .unsigned_rtp = true,
    .allow_210hz_fallback = false,
};

static const lra_driver_t lra1030z = {
    .port = I2C_NUM_1,
    .sda = GPIO_NUM_32,
    .scl = GPIO_NUM_33,
    .name = "1030Z",
    .rated_voltage = 0x48,
    .overdrive_clamp = 0x7B,
    .feedback = 0xAA,
    .control1 = 0x93,
    .control2_cal = 0xF5,
    .control2_rtp = 0x75,
    .control3_rtp = 0x80,
    .control4 = 0x30,
    .control5 = 0x80,
    .unsigned_rtp = false,
    .allow_210hz_fallback = true,
};

static esp_err_t write_reg(
    const lra_driver_t *driver,
    uint8_t reg,
    uint8_t value)
{
    uint8_t data[2] = {reg, value};
    return i2c_master_write_to_device(
        driver->port,
        DRV2605L_ADDR,
        data,
        sizeof(data),
        pdMS_TO_TICKS(100));
}

static esp_err_t read_reg(
    const lra_driver_t *driver,
    uint8_t reg,
    uint8_t *value)
{
    return i2c_master_write_read_device(
        driver->port,
        DRV2605L_ADDR,
        &reg,
        1,
        value,
        1,
        pdMS_TO_TICKS(100));
}

static void init_i2c(const lra_driver_t *driver)
{
    i2c_config_t config = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = driver->sda,
        .scl_io_num = driver->scl,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_FREQ_HZ,
    };
    ESP_ERROR_CHECK(i2c_param_config(driver->port, &config));
    ESP_ERROR_CHECK(
        i2c_driver_install(driver->port, config.mode, 0, 0, 0));
}

static void init_serial(void)
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
    ESP_ERROR_CHECK(
        uart_set_pin(
            UART_NUM_0,
            UART_PIN_NO_CHANGE,
            UART_PIN_NO_CHANGE,
            UART_PIN_NO_CHANGE,
            UART_PIN_NO_CHANGE));
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 2048, 0, 0, NULL, 0));
}

static void stop_driver(const lra_driver_t *driver)
{
    ESP_ERROR_CHECK(write_reg(driver, REG_RTP_INPUT, 0));
    ESP_ERROR_CHECK(write_reg(driver, REG_MODE, MODE_STANDBY));
}

static uint8_t rtp_value(const lra_driver_t *driver, uint8_t level)
{
    if (level == 0) {
        return 0;
    }
    if (!driver->unsigned_rtp) {
        return level;
    }
    return (uint8_t)(((uint16_t)level * 255U + 63U) / 127U);
}

static void set_level(const lra_driver_t *driver, uint8_t level)
{
    if (level == 0) {
        stop_driver(driver);
        return;
    }
    ESP_ERROR_CHECK(write_reg(driver, REG_MODE, MODE_RTP));
    ESP_ERROR_CHECK(write_reg(driver, REG_RTP_INPUT, rtp_value(driver, level)));
}

static bool calibrate(const lra_driver_t *driver)
{
    ESP_LOGI(TAG, "calibrating %s LRA", driver->name);
    ESP_ERROR_CHECK(write_reg(driver, REG_RTP_INPUT, 0));
    ESP_ERROR_CHECK(write_reg(driver, REG_MODE, MODE_AUTOCAL));
    ESP_ERROR_CHECK(
        write_reg(driver, REG_RATED_VOLTAGE, driver->rated_voltage));
    ESP_ERROR_CHECK(
        write_reg(driver, REG_OD_CLAMP, driver->overdrive_clamp));
    ESP_ERROR_CHECK(write_reg(driver, REG_FEEDBACK, driver->feedback));
    ESP_ERROR_CHECK(write_reg(driver, REG_CONTROL1, driver->control1));
    ESP_ERROR_CHECK(
        write_reg(driver, REG_CONTROL2, driver->control2_cal));
    ESP_ERROR_CHECK(
        write_reg(driver, REG_CONTROL3, driver->control3_rtp));
    ESP_ERROR_CHECK(write_reg(driver, REG_CONTROL4, driver->control4));
    ESP_ERROR_CHECK(write_reg(driver, REG_CONTROL5, driver->control5));
    ESP_ERROR_CHECK(write_reg(driver, REG_GO, 1));

    uint8_t go = 1;
    for (int elapsed = 0; elapsed < 1800; elapsed += 10) {
        vTaskDelay(pdMS_TO_TICKS(10));
        ESP_ERROR_CHECK(read_reg(driver, REG_GO, &go));
        if ((go & 1) == 0) {
            break;
        }
    }

    uint8_t status = 0;
    ESP_ERROR_CHECK(read_reg(driver, REG_STATUS, &status));
    if ((go & 1) != 0 || (status & STATUS_DIAG_FAIL) != 0) {
        if (!driver->allow_210hz_fallback) {
            ESP_LOGE(
                TAG,
                "%s calibration failed (GO=0x%02X STATUS=0x%02X)",
                driver->name,
                go,
                status);
            stop_driver(driver);
            return false;
        }
        ESP_ERROR_CHECK(write_reg(driver, REG_OL_LRA_PERIOD, 0x30));
        ESP_ERROR_CHECK(write_reg(driver, REG_CONTROL3, 0x81));
        ESP_ERROR_CHECK(write_reg(driver, REG_MODE, MODE_RTP));
        ESP_ERROR_CHECK(write_reg(driver, REG_RTP_INPUT, 0));
        ESP_LOGW(TAG, "%s using fixed 210 Hz fallback", driver->name);
        return true;
    }

    uint8_t comp = 0;
    uint8_t bemf = 0;
    uint8_t period = 0;
    ESP_ERROR_CHECK(read_reg(driver, REG_AUTOCAL_COMP, &comp));
    ESP_ERROR_CHECK(read_reg(driver, REG_AUTOCAL_BEMF, &bemf));
    ESP_ERROR_CHECK(read_reg(driver, REG_LRA_PERIOD, &period));
    ESP_ERROR_CHECK(
        write_reg(driver, REG_CONTROL2, driver->control2_rtp));
    ESP_ERROR_CHECK(
        write_reg(driver, REG_CONTROL3, driver->control3_rtp));
    ESP_ERROR_CHECK(write_reg(driver, REG_MODE, MODE_RTP));
    ESP_ERROR_CHECK(write_reg(driver, REG_RTP_INPUT, 0));
    ESP_LOGI(
        TAG,
        "%s ready: COMP=0x%02X BEMF=0x%02X PERIOD=0x%02X",
        driver->name,
        comp,
        bemf,
        period);
    return true;
}

static void confirmation_pulse(
    const lra_driver_t *driver,
    bool ready,
    uint8_t level,
    int duration_ms)
{
    if (!ready) {
        return;
    }
    set_level(driver, level);
    vTaskDelay(pdMS_TO_TICKS(duration_ms));
    set_level(driver, 0);
}

static bool read_command(uint8_t *level, bool *for_1030z)
{
    static char line[16];
    static size_t length = 0;
    uint8_t c;
    while (uart_read_bytes(UART_NUM_0, &c, 1, 0) == 1) {
        if (c == '\n' || c == '\r') {
            line[length] = '\0';
            if (length > 1 && (line[0] == 'H' || line[0] == 'L')) {
                int requested = atoi(&line[1]);
                if (requested < 0) {
                    requested = 0;
                }
                if (requested > 127) {
                    requested = 127;
                }
                *level = (uint8_t)requested;
                *for_1030z = line[0] == 'L';
                /*
                 * Return immediately after one valid line. Any following
                 * command remains in the UART driver's receive buffer for
                 * the next 1 ms loop iteration. Previously this loop consumed
                 * an H line and an L line together, then returned only the
                 * final L command, silently dropping every 0832 update.
                 */
                length = 0;
                return true;
            }
            length = 0;
        } else if (length < sizeof(line) - 1) {
            line[length++] = (char)c;
        }
    }
    return false;
}

void app_main(void)
{
    init_i2c(&lra0832);
    init_i2c(&lra1030z);
    stop_driver(&lra0832);
    stop_driver(&lra1030z);
    init_serial();

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
        "reset-safe stop: press BOOT; H=0832 and L=1030Z");

    bool enabled = false;
    bool ready_0832 = false;
    bool ready_1030z = false;
    bool serial_active = false;
    TickType_t last_command = xTaskGetTickCount();

    while (1) {
        if (gpio_get_level(BOOT_GPIO) == 0) {
            ready_0832 = calibrate(&lra0832);
            ready_1030z = calibrate(&lra1030z);
            confirmation_pulse(&lra0832, ready_0832, 88, 180);
            vTaskDelay(pdMS_TO_TICKS(120));
            confirmation_pulse(&lra1030z, ready_1030z, 90, 100);
            enabled = ready_0832 || ready_1030z;
            serial_active = false;
            while (gpio_get_level(BOOT_GPIO) == 0) {
                vTaskDelay(pdMS_TO_TICKS(20));
            }
            ESP_LOGI(
                TAG,
                "enabled: H 0832=%s; L 1030Z=%s",
                ready_0832 ? "ready" : "failed",
                ready_1030z ? "ready" : "failed");
        }

        uint8_t level = 0;
        bool for_1030z = false;
        if (read_command(&level, &for_1030z)) {
            if (enabled) {
                if (for_1030z && ready_1030z) {
                    set_level(&lra1030z, level);
                }
                if (!for_1030z && ready_0832) {
                    set_level(&lra0832, level);
                }
                last_command = xTaskGetTickCount();
                serial_active = true;
            } else if (level > 0) {
                ESP_LOGW(TAG, "ignored command while reset-latched off");
            }
        }

        if (
            serial_active
            && xTaskGetTickCount() - last_command
                >= pdMS_TO_TICKS(SERIAL_TIMEOUT_MS)) {
            if (ready_0832) {
                set_level(&lra0832, 0);
            }
            if (ready_1030z) {
                set_level(&lra1030z, 0);
            }
            serial_active = false;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
