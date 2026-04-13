# CM4 핀맵 및 GPIO 매핑

## 1. UART 핀 매핑

### UART → GPIO 매핑표

| UART | 디바이스 | TX GPIO | RX GPIO | CTS GPIO | RTS GPIO | 오버레이 |
|------|---------|---------|---------|----------|----------|---------|
| UART0 | /dev/ttyAMA0 | GPIO 14 | GPIO 15 | GPIO 16 | GPIO 17 | 기본 (disable-bt로 활성화) |
| UART1 | /dev/ttyS0 | GPIO 14 | GPIO 15 | - | - | 미니 UART (기본 콘솔) |
| UART2 | /dev/ttyAMA1 | GPIO 0 | GPIO 1 | GPIO 2 | GPIO 3 | dtoverlay=uart2 |
| UART3 | /dev/ttyAMA2 | GPIO 4 | GPIO 5 | GPIO 6 | GPIO 7 | dtoverlay=uart3 |
| UART4 | /dev/ttyAMA3 | GPIO 8 | GPIO 9 | GPIO 10 | GPIO 11 | dtoverlay=uart4 |
| UART5 | /dev/ttyAMA4 | GPIO 12 | GPIO 13 | GPIO 14 | GPIO 15 | dtoverlay=uart5 |

### UART 타입

| 타입 | UART | 특징 |
|------|------|------|
| PL011 (16550 호환) | UART0, 2, 3, 4, 5 | 하드웨어 UART, 안정적 보드레이트, CTS/RTS 지원 |
| Mini UART | UART1 | 소프트웨어 기반, 보드레이트가 CPU 클럭에 의존, RS485 부적합 |

### RTU 프로젝트 UART 할당

| 채널 | UART | 디바이스 | GPIO TX/RX | 용도 |
|------|------|---------|-----------|------|
| CH0 | UART0 | /dev/ttyAMA0 | GPIO 14/15 | 디버거 (시리얼 콘솔) |
| CH1 | UART3 | /dev/ttyAMA3 | GPIO 4/5 | 설비 통신 (인버터/릴레이/기상) |
| CH2 | UART4 | /dev/ttyAMA4 | GPIO 8/9 | DER-AVM 마스터 연결 |
| CH3 | UART5 | /dev/ttyAMA5 | GPIO 12/13 | 예비 |

## 2. SPI 핀 매핑

| SPI 버스 | 신호 | GPIO | 비고 |
|---------|------|------|------|
| SPI0 | MOSI | GPIO 10 | **UART4 CTS와 충돌** |
| SPI0 | MISO | GPIO 9 | **UART4 RX와 충돌** |
| SPI0 | SCLK | GPIO 11 | **UART4 RTS와 충돌** |
| SPI0 | CE0 | GPIO 8 | **UART4 TX와 충돌** |
| SPI0 | CE1 | GPIO 7 | UART3 RTS와 충돌 |
| SPI1 | MOSI | GPIO 20 | |
| SPI1 | MISO | GPIO 19 | |
| SPI1 | SCLK | GPIO 21 | |
| SPI1 | CE0 | GPIO 18 | |
| SPI1 | CE1 | GPIO 17 | |
| SPI1 | CE2 | GPIO 16 | |

**주의**: SPI0과 UART4는 동시 사용 불가! RTU 프로젝트에서 CH2(UART4)를 사용하므로 SPI0은 사용 불가.

## 3. I2C 핀 매핑

| I2C 버스 | SDA GPIO | SCL GPIO | 비고 |
|---------|----------|----------|------|
| I2C0 | GPIO 0 | GPIO 1 | UART2 TX/RX와 충돌 |
| I2C1 | GPIO 2 | GPIO 3 | 기본 사용자 I2C, 1.8K 풀업 내장 |
| I2C0 (대체) | GPIO 44 | GPIO 45 | 카메라/디스플레이용 |

## 4. 핀 충돌 요약 (RTU 프로젝트 관련)

| GPIO | 기본 기능 | 대체 기능 | 충돌 여부 |
|------|----------|----------|----------|
| GPIO 0-1 | I2C0 | UART2 | I2C0 사용 시 UART2 불가 |
| GPIO 2-3 | I2C1 | UART3 CTS/RTS | I2C1은 사용 가능 (플로우 컨트롤 미사용) |
| GPIO 4-5 | - | UART3 TX/RX | **CH1 사용** |
| GPIO 8-9 | SPI0 | UART4 TX/RX | **CH2 사용, SPI0 불가** |
| GPIO 10-11 | SPI0 | UART4 CTS/RTS | SPI0 불가 |
| GPIO 12-13 | PWM0/1 | UART5 TX/RX | **CH3 사용, PWM 불가** |
| GPIO 14-15 | UART0 | UART5 CTS/RTS | **CH0 사용** |

## 5. 이더넷 핀

CM4는 Gigabit Ethernet PHY (BCM54210PE)를 내장하고 있으며,
메자닌 커넥터 J2를 통해 4쌍 차동 신호로 연결됩니다:

| 신호 | 설명 |
|------|------|
| ETH_TRD0_P / ETH_TRD0_N | Pair 0 |
| ETH_TRD1_P / ETH_TRD1_N | Pair 1 |
| ETH_TRD2_P / ETH_TRD2_N | Pair 2 |
| ETH_TRD3_P / ETH_TRD3_N | Pair 3 |
| ETH_LED0, ETH_LED1 | Link/Activity LED |

## 6. 참고 링크

| 자료 | URL |
|------|-----|
| CM4 데이터시트 (핀맵 섹션 4) | https://datasheets.raspberrypi.com/cm4/cm4-datasheet.pdf |
| UART 핀아웃 | https://pinout.xyz/pinout/uart |
| SPI 핀아웃 | https://pinout.xyz/pinout/spi |
| CM4 다중 UART 설정 | https://forums.raspberrypi.com/viewtopic.php?t=354412 |
| Pi4J CM4 핀 번호 | https://www.pi4j.com/1.3/pins/rpi-cm4.html |
