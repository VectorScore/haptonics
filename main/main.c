#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include "driver/i2c.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* Two physical I2C buses are required: DRV2605L has the fixed address 0x5A. */
#define ERM_I2C_PORT I2C_NUM_0
#define ERM_I2C_SDA 21
#define ERM_I2C_SCL 22
#define LRA_I2C_PORT I2C_NUM_1
#define LRA_I2C_SDA 32
#define LRA_I2C_SCL 33
#define I2C_FREQ_HZ 400000
#define BOOT_GPIO GPIO_NUM_0
#define SERIAL_BAUD 115200
#define SERIAL_TIMEOUT_MS 500
#define DRV2605L_ADDR 0x5A
#define BOOT_CHECK_ERM_LEVEL 127
#define BOOT_CHECK_ERM_MS 120
#define BOOT_CHECK_LRA_LEVEL 90
#define BOOT_CHECK_LRA_MS 60

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
#define REG_OL_LRA_PERIOD 0x20
#define REG_LRA_PERIOD 0x22

#define MODE_STANDBY 0x40
#define MODE_RTP 0x05
#define MODE_AUTOCAL 0x07

typedef struct {
    i2c_port_t port;
    int sda;
    int scl;
    const char *name;
} drv2605l_t;

static const char *TAG = "haptic";
static const drv2605l_t erm = { ERM_I2C_PORT, ERM_I2C_SDA, ERM_I2C_SCL, "ERM" };
static const drv2605l_t lra = { LRA_I2C_PORT, LRA_I2C_SDA, LRA_I2C_SCL, "LRA" };

static esp_err_t write_reg(const drv2605l_t *driver, uint8_t reg, uint8_t value)
{
    uint8_t data[2] = { reg, value };
    return i2c_master_write_to_device(driver->port, DRV2605L_ADDR, data,
                                      sizeof(data), pdMS_TO_TICKS(100));
}

static esp_err_t read_reg(const drv2605l_t *driver, uint8_t reg, uint8_t *value)
{
    return i2c_master_write_read_device(driver->port, DRV2605L_ADDR, &reg, 1,
                                        value, 1, pdMS_TO_TICKS(100));
}

static esp_err_t init_i2c(const drv2605l_t *driver)
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
    return i2c_driver_install(driver->port, config.mode, 0, 0, 0);
}

static esp_err_t init_serial(void)
{
    uart_config_t config = {
        .baud_rate = SERIAL_BAUD, .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE, .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_APB,
    };
    uart_driver_delete(UART_NUM_0);
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &config));
    ESP_ERROR_CHECK(uart_set_pin(UART_NUM_0, UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE));
    return uart_driver_install(UART_NUM_0, 2048, 0, 0, NULL, 0);
}

static void stop(const drv2605l_t *driver)
{
    ESP_ERROR_CHECK(write_reg(driver, REG_RTP_INPUT, 0));
    ESP_ERROR_CHECK(write_reg(driver, REG_MODE, MODE_STANDBY));
}

static void start_erm(void)
{
    ESP_ERROR_CHECK(write_reg(&erm, REG_FEEDBACK, 0x36));
    ESP_ERROR_CHECK(write_reg(&erm, REG_CONTROL3, 0xA0));
    ESP_ERROR_CHECK(write_reg(&erm, REG_MODE, MODE_RTP));
    ESP_ERROR_CHECK(write_reg(&erm, REG_RTP_INPUT, 0));
}

static void set_erm(uint8_t level)
{
    ESP_ERROR_CHECK(write_reg(&erm, REG_MODE, MODE_RTP));
    ESP_ERROR_CHECK(write_reg(&erm, REG_RTP_INPUT, level));
}

static bool start_lra_and_calibrate(void)
{
    /* JYLRA1030Z: 1.8 Vrms, 210 Hz, 100 mA max.  Closed-loop auto-resonance
       tracks its actual mounted resonance; RTP only controls amplitude. */
    ESP_ERROR_CHECK(write_reg(&lra, REG_RTP_INPUT, 0));
    ESP_ERROR_CHECK(write_reg(&lra, REG_MODE, MODE_AUTOCAL));
    /* 0x48 is 1.8 Vrms at 210 Hz with the 300 us sample time below. */
    ESP_ERROR_CHECK(write_reg(&lra, REG_RATED_VOLTAGE, 0x48));
    /* 0x7B is 2.61 V peak, the motor's 1.85 Vrms operating ceiling. */
    ESP_ERROR_CHECK(write_reg(&lra, REG_OD_CLAMP, 0x7B));
    /* LRA; brake factor 2; loop gain 2; initial BEMF gain 2. */
    ESP_ERROR_CHECK(write_reg(&lra, REG_FEEDBACK, 0xAA));
    /* Startup boost and 2.4 ms initial half-period for the 210 Hz LRA. */
    ESP_ERROR_CHECK(write_reg(&lra, REG_CONTROL1, 0x93));
    /* 300 us sample; short blanking and current-dissipation windows. */
    ESP_ERROR_CHECK(write_reg(&lra, REG_CONTROL2, 0xF5));
    /* Closed-loop auto-resonance during calibration. */
    ESP_ERROR_CHECK(write_reg(&lra, REG_CONTROL3, 0x80));
    /* Longest auto-calibration time; leave OTP programming disabled. */
    ESP_ERROR_CHECK(write_reg(&lra, REG_CONTROL4, 0x30));
    ESP_ERROR_CHECK(write_reg(&lra, REG_GO, 1));

    for (int elapsed = 0; elapsed < 1500; elapsed += 10) {
        uint8_t go = 0;
        ESP_ERROR_CHECK(read_reg(&lra, REG_GO, &go));
        if ((go & 1) == 0) {
            uint8_t status = 0;
            ESP_ERROR_CHECK(read_reg(&lra, REG_STATUS, &status));
            if (status & 0x08) {
                ESP_LOGE(TAG, "LRA auto-calibration failed (STATUS=0x%02X)", status);
                /* A fixed 210 Hz open-loop fallback proves the output and
                   motor wiring even if back-EMF calibration cannot lock. */
                ESP_ERROR_CHECK(write_reg(&lra, REG_OL_LRA_PERIOD, 0x30));
                /* Signed RTP is essential in bidirectional open-loop mode:
                   0x00 is no drive, while unsigned 0x00 is negative full-scale. */
                ESP_ERROR_CHECK(write_reg(&lra, REG_CONTROL3, 0x81));
                ESP_ERROR_CHECK(write_reg(&lra, REG_MODE, MODE_RTP));
                ESP_ERROR_CHECK(write_reg(&lra, REG_RTP_INPUT, 0));
                ESP_LOGW(TAG, "LRA fallback: fixed 210 Hz open-loop, signed RTP");
                return true;
            }
            uint8_t comp = 0, bemf = 0, period = 0;
            ESP_ERROR_CHECK(read_reg(&lra, REG_AUTOCAL_COMP, &comp));
            ESP_ERROR_CHECK(read_reg(&lra, REG_AUTOCAL_BEMF, &bemf));
            ESP_ERROR_CHECK(read_reg(&lra, REG_LRA_PERIOD, &period));
            /* Signed RTP keeps 0x00 as the no-drive value. */
            ESP_ERROR_CHECK(write_reg(&lra, REG_CONTROL3, 0x80));
            ESP_ERROR_CHECK(write_reg(&lra, REG_MODE, MODE_RTP));
            ESP_ERROR_CHECK(write_reg(&lra, REG_RTP_INPUT, 0));
            ESP_LOGI(TAG,
                     "LRA calibrated: COMP=0x%02X BEMF=0x%02X PERIOD=0x%02X",
                     comp, bemf, period);
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    ESP_LOGE(TAG, "LRA auto-calibration timed out");
    return false;
}

static void set_lra(uint8_t level)
{
    if (level == 0) {
        /* Hard-stop the output bridge at silence.  Some LRAs can keep ringing
           while RTP remains selected even when its signed amplitude is zero. */
        ESP_ERROR_CHECK(write_reg(&lra, REG_RTP_INPUT, 0));
        ESP_ERROR_CHECK(write_reg(&lra, REG_MODE, MODE_STANDBY));
        return;
    }

    /* Load the new amplitude before waking RTP so an old value cannot pulse. */
    ESP_ERROR_CHECK(write_reg(&lra, REG_RTP_INPUT, level));
    ESP_ERROR_CHECK(write_reg(&lra, REG_MODE, MODE_RTP));
}

static void boot_enable_test(bool lra_ready)
{
    ESP_LOGI(TAG, "BOOT: short calibration check");
    start_erm();
    set_erm(BOOT_CHECK_ERM_LEVEL);
    vTaskDelay(pdMS_TO_TICKS(BOOT_CHECK_ERM_MS));
    set_erm(0);
    if (lra_ready) {
        ESP_LOGI(TAG, "BOOT: LRA calibration check");
        set_lra(BOOT_CHECK_LRA_LEVEL);
        vTaskDelay(pdMS_TO_TICKS(BOOT_CHECK_LRA_MS));
        set_lra(0);
    }
}

static bool read_command(uint8_t *level, bool *for_lra)
{
    static char line[16];
    static size_t length = 0;
    uint8_t c;
    bool received = false;
    while (uart_read_bytes(UART_NUM_0, &c, 1, 0) == 1) {
        if (c == '\n' || c == '\r') {
            line[length] = '\0';
            if (length > 1 && (line[0] == 'H' || line[0] == 'L')) {
                int requested = atoi(&line[1]);
                if (requested < 0) requested = 0;
                if (requested > 127) requested = 127;
                *level = (uint8_t)requested;
                *for_lra = line[0] == 'L';
                received = true;
            }
            length = 0;
        } else if (length < sizeof(line) - 1) {
            line[length++] = (char)c;
        }
    }
    return received;
}

void app_main(void)
{
    ESP_ERROR_CHECK(init_i2c(&erm));
    ESP_ERROR_CHECK(init_i2c(&lra));
    stop(&erm);
    stop(&lra);
    ESP_ERROR_CHECK(init_serial());

    gpio_config_t boot_button = {
        .pin_bit_mask = 1ULL << BOOT_GPIO, .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE, .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&boot_button));
    ESP_LOGI(TAG, "reset-safe stop: press BOOT; then H=ERM and L=LRA, 0..127");

    bool enabled = false;
    bool lra_ready = false;
    bool serial_active = false;
    TickType_t last_command = xTaskGetTickCount();

    while (1) {
        if (gpio_get_level(BOOT_GPIO) == 0) {
            lra_ready = start_lra_and_calibrate();
            boot_enable_test(lra_ready);
            enabled = true;
            serial_active = false;
            while (gpio_get_level(BOOT_GPIO) == 0) vTaskDelay(pdMS_TO_TICKS(20));
            ESP_LOGI(TAG, "enabled: H0..H127=ERM; L0..L127=LRA");
        }

        uint8_t level = 0;
        bool for_lra = false;
        if (read_command(&level, &for_lra)) {
            if (enabled) {
                if (for_lra && lra_ready) set_lra(level);
                if (!for_lra) set_erm(level);
                last_command = xTaskGetTickCount();
                serial_active = true;
            } else if (level > 0) {
                ESP_LOGW(TAG, "ignored command while reset-latched off");
            }
        }
        if (serial_active && xTaskGetTickCount() - last_command >= pdMS_TO_TICKS(SERIAL_TIMEOUT_MS)) {
            set_erm(0);
            if (lra_ready) set_lra(0);
            serial_active = false;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
