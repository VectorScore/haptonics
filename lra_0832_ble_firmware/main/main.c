#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "nvs_flash.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_gap.h"
#include "host/ble_gatt.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

/*
 * BLE variant of the DRV2605L firmware for a two-wire 0832 LRA.
 *
 * Same motor code as lra_0832_firmware (I2C SDA=GPIO11 / SCL=GPIO10 on the
 * ESP32-S3-Touch-LCD-1.46 J9 header), with a NimBLE GATT server exposing the
 * Nordic UART Service so the web app can connect over Web Bluetooth.
 *
 * Accepts "H<0..127>\n" lines from BOTH the USB serial console and the BLE
 * Nordic UART RX characteristic. Whichever link sent the last command owns
 * the 500 ms watchdog: no command for 500 ms -> motor stopped.
 *
 * Safety flow unchanged: the LRA is reset-latched off until BOOT is pressed
 * (calibrate + enable). BLE clients get notified about the state on connect.
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
#define LRA_DEFAULT_DRIVE_TIME_REG LRA_205_DRIVE_TIME_REG
#define LRA_DEFAULT_RATED_VOLTAGE  LRA_205_RATED_VOLTAGE_REG
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

/* ------------------------------------------------------------------------ */
/* BLE: Nordic UART Service (same UUIDs the web app connects to).           */
/* ------------------------------------------------------------------------ */
#define BLE_DEVICE_NAME "HapticMusic"

/* 6e400001-b5a3-f393-e0a9-e50e24dcca9e, little-endian byte order. */
static const ble_uuid128_t nus_svc_uuid =
    BLE_UUID128_INIT(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                     0x93, 0xf3, 0xa3, 0xb5, 0x01, 0x00, 0x40, 0x6e);
static const ble_uuid128_t nus_rx_uuid =
    BLE_UUID128_INIT(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                     0x93, 0xf3, 0xa3, 0xb5, 0x02, 0x00, 0x40, 0x6e);
static const ble_uuid128_t nus_tx_uuid =
    BLE_UUID128_INIT(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                     0x93, 0xf3, 0xa3, 0xb5, 0x03, 0x00, 0x40, 0x6e);

static uint16_t ble_conn_handle = BLE_HS_CONN_HANDLE_NONE;
static uint16_t nus_tx_val_handle = 0;
static QueueHandle_t ble_level_queue = NULL;

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

/* ------------------------------------------------------------------------ */
/* BLE: GATT server (Nordic UART Service)                                   */
/* ------------------------------------------------------------------------ */

static void ble_notify(const char *msg)
{
    if (ble_conn_handle == BLE_HS_CONN_HANDLE_NONE || nus_tx_val_handle == 0) {
        return;
    }
    struct os_mbuf *om =
        ble_hs_mbuf_from_flat((const uint8_t *)msg, strlen(msg));
    if (om != NULL) {
        ble_gatts_notify_custom(ble_conn_handle, nus_tx_val_handle, om);
    }
}

/* Parse "H<level>\n" bytes arriving over BLE (NimBLE host task context). */
static void ble_feed_bytes(const uint8_t *data, uint16_t len)
{
    static char line[16];
    static size_t length = 0;

    for (uint16_t i = 0; i < len; ++i) {
        char c = (char)data[i];
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
                uint8_t value = (uint8_t)level;
                if (ble_level_queue != NULL) {
                    xQueueSend(ble_level_queue, &value, 0);
                }
            }
            length = 0;
        } else if (length < sizeof(line) - 1) {
            line[length++] = c;
        }
    }
}

static int nus_chr_access(
    uint16_t conn_handle,
    uint16_t attr_handle,
    struct ble_gatt_access_ctxt *ctxt,
    void *arg)
{
    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR && (intptr_t)arg == 0) {
        uint8_t buf[64];
        uint16_t out_len = 0;
        int rc =
            ble_hs_mbuf_to_flat(ctxt->om, buf, sizeof(buf) - 1, &out_len);
        if (rc != 0) {
            return BLE_ATT_ERR_INSUFFICIENT_RES;
        }
        ble_feed_bytes(buf, out_len);
    }
    return 0;
}

static const struct ble_gatt_svc_def gatt_svs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &nus_svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]){
            {
                /* web app -> firmware: "H<0..127>\n" lines */
                .uuid = &nus_rx_uuid.u,
                .access_cb = nus_chr_access,
                .arg = (void *)(intptr_t)0,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                /* firmware -> web app: status notifications */
                .uuid = &nus_tx_uuid.u,
                .access_cb = nus_chr_access,
                .arg = (void *)(intptr_t)1,
                .val_handle = &nus_tx_val_handle,
                .flags = BLE_GATT_CHR_F_NOTIFY,
            },
            { 0 },
        },
    },
    { 0 },
};

static void ble_advertise_start(void);

int ble_gap_event(struct ble_gap_event *event, void *arg);

static void ble_advertise_start(void)
{
    struct ble_hs_adv_fields fields;
    memset(&fields, 0, sizeof(fields));
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = (ble_uuid128_t *)&nus_svc_uuid;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;
    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "advertise fields failed: rc=%d", rc);
        return;
    }

    /* Scan response carries the human-readable name for the browser picker. */
    struct ble_hs_adv_fields rsp;
    memset(&rsp, 0, sizeof(rsp));
    rsp.name = (const uint8_t *)BLE_DEVICE_NAME;
    rsp.name_len = strlen(BLE_DEVICE_NAME);
    rsp.name_is_complete = 1;
    rc = ble_gap_adv_rsp_set_fields(&rsp);
    if (rc != 0) {
        ESP_LOGE(TAG, "scan response fields failed: rc=%d", rc);
        return;
    }

    struct ble_gap_adv_params params;
    memset(&params, 0, sizeof(params));
    params.conn_mode = BLE_GAP_CONN_MODE_UND;
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    rc = ble_gap_adv_start(
        BLE_OWN_ADDR_PUBLIC, NULL, BLE_HS_FOREVER, &params, ble_gap_event, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "advertise start failed: rc=%d", rc);
    }
}

int ble_gap_event(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status != 0) {
            ble_conn_handle = BLE_HS_CONN_HANDLE_NONE;
            ble_advertise_start();
        } else {
            ble_conn_handle = event->connect.conn_handle;
            ESP_LOGI(TAG, "BLE client connected");
        }
        return 0;

    case BLE_GAP_EVENT_DISCONNECT:
        ble_conn_handle = BLE_HS_CONN_HANDLE_NONE;
        ESP_LOGI(TAG, "BLE client disconnected, restarting advertising");
        ble_advertise_start();
        return 0;

    case BLE_GAP_EVENT_ADV_COMPLETE:
        ble_advertise_start();
        return 0;

    default:
        return 0;
    }
}

static void ble_on_sync(void)
{
    ble_advertise_start();
}

static void ble_host_task(void *param)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static void ble_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES
        || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    ble_level_queue = xQueueCreate(16, sizeof(uint8_t));

    ESP_ERROR_CHECK(nimble_port_init());
    ble_hs_cfg.sync_cb = ble_on_sync;

    ESP_ERROR_CHECK(ble_gatts_count_cfg(gatt_svs));
    ESP_ERROR_CHECK(ble_gatts_add_svcs(gatt_svs));
    ble_svc_gap_device_name_set(BLE_DEVICE_NAME);
    nimble_port_freertos_init(ble_host_task);

    ESP_LOGI(TAG, "BLE ready: advertising as %s (Nordic UART)", BLE_DEVICE_NAME);
}

/* ------------------------------------------------------------------------ */
/* Main loop                                                                 */
/* ------------------------------------------------------------------------ */

void app_main(void)
{
    ESP_ERROR_CHECK(i2c_init());
    drv_stop();
    ESP_ERROR_CHECK(serial_init());
    ble_init();

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
    bool link_active = false;
    bool disabled_command_logged = false;
    uint8_t current_level = 0;
    unsigned long command_count = 0;
    TickType_t last_link_command = xTaskGetTickCount();
    TickType_t last_status = xTaskGetTickCount();

    while (1) {
        if (gpio_get_level(BOOT_GPIO) == 0) {
            motor_enabled = boot_enable_lra();
            link_active = false;
            while (gpio_get_level(BOOT_GPIO) == 0) {
                vTaskDelay(pdMS_TO_TICKS(20));
            }
            if (motor_enabled) {
                ESP_LOGI(TAG, "0832 LRA enabled: accepting H0..H127");
                ble_notify("LRA enabled: H0..H127 accepted");
            }
        }

        /* Serial link */
        uint8_t requested_level = 0;
        if (serial_read_level(&requested_level)) {
            if (motor_enabled) {
                drv_set_level(requested_level);
                current_level = requested_level;
                command_count++;
                last_link_command = xTaskGetTickCount();
                link_active = true;
            } else {
                link_active = false;
                if (requested_level > 0 && !disabled_command_logged) {
                    ESP_LOGW(
                        TAG,
                        "ignored H%u: LRA remains reset-latched off",
                        requested_level);
                    disabled_command_logged = true;
                }
            }
        }

        /* BLE link */
        if (ble_level_queue != NULL) {
            uint8_t ble_level = 0;
            while (xQueueReceive(ble_level_queue, &ble_level, 0)) {
                if (motor_enabled) {
                    drv_set_level(ble_level);
                    current_level = ble_level;
                    command_count++;
                    last_link_command = xTaskGetTickCount();
                    link_active = true;
                } else {
                    link_active = false;
                    if (ble_level > 0 && !disabled_command_logged) {
                        ESP_LOGW(
                            TAG,
                            "ignored BLE H%u: LRA remains reset-latched off",
                            ble_level);
                        disabled_command_logged = true;
                        ble_notify(
                            "motor disabled: press BOOT to calibrate+enable");
                    }
                }
            }
        }

        if (link_active
            && (xTaskGetTickCount() - last_link_command)
                >= pdMS_TO_TICKS(SERIAL_TIMEOUT_MS)) {
            drv_set_level(0);
            current_level = 0;
            link_active = false;
            ESP_LOGW(TAG, "link timeout: motor stopped");
        }

        if ((xTaskGetTickCount() - last_status) >= pdMS_TO_TICKS(1000)) {
            ESP_LOGI(
                TAG,
                "heartbeat: enabled=%s level=%u commands=%lu link=%s ble=%s",
                motor_enabled ? "yes" : "no",
                current_level,
                command_count,
                link_active ? "active" : "idle",
                ble_conn_handle != BLE_HS_CONN_HANDLE_NONE ? "connected"
                                                           : "advertising");
            last_status = xTaskGetTickCount();
        }

        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
