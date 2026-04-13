# CM4-ETH-RS485-BASE-B 보드 스펙

## 1. 제품 개요

| 항목 | 내용 |
|------|------|
| 제조사 | Waveshare Electronics (중국 심천) |
| 제품명 | CM4-ETH-RS485-BASE-B |
| 용도 | CM4 캐리어 보드 (4채널 절연 RS485 + 듀얼 이더넷) |
| 가격 | ~$57.99 USD |
| 박스 버전 | CM4-ETH-RS485-BOX-B (금속 케이스 포함) |

## 2. 보드 사양

| 항목 | 사양 |
|------|------|
| 크기 | 93.6 x 108.8 mm |
| 무게 | 100 g |
| 전원 입력 | 7~36V DC (스크류 터미널) 또는 5V USB-C |
| CM4 소켓 | 표준 2x100핀 메자닌 커넥터 (전 CM4 모델 호환) |
| RS485 | 4채널 절연 (COM0~COM3), 5.08mm 스크류 터미널 |
| Ethernet 0 | 기가비트 (1000Mbps) — CM4 네이티브 RGMII |
| Ethernet 1 | 100Mbps — USB 확장 (RTL8152B) |
| USB | USB 2.0 Type-A x 2 |
| HDMI | 1x (4Kp30) |
| 카메라 | MIPI CSI-2 x 2 (15핀 1.0mm FPC) |
| RTC | PCF85063 (I2C, CR1220 배터리, 웨이크업 지원) |
| 보안칩 | ATSHA204 (기본 비활성) |
| 팬 | 5V PWM 헤더 (GPIO18 제어) |
| SD카드 | MicroSD 슬롯 (CM4 Lite용) |
| 부저 | GPIO22 |
| 사용자 LED | GPIO20, GPIO26 |

## 3. RS485 트랜시버

| 항목 | 사양 |
|------|------|
| 트랜시버 IC | SP3485 (MaxLinear, 3.3V 반이중 RS-485, 10Mbps) |
| 절연 | 디지털 아이솔레이터 (채널별 갈바닉 절연) |
| 종단 저항 | 120옴 (점퍼캡으로 선택 가능) |
| 커넥터 | 5.08mm 스크류 터미널 (A, B, GND) |
| LED | 채널당 TX/RX LED 각 1개 (총 8개) |

### RS485 방향 제어 모드

| 모드 | 설명 | 설정 |
|------|------|------|
| **Full-auto (기본)** | 하드웨어 자동 TX/RX 전환, 소프트웨어 제어 불필요 | PCB 뒷면 0옴 저항 기본 위치 |
| **Semi-auto** | 소프트웨어로 DE/RE 핀 제어, 부하 용량 강화 | PCB 뒷면 0옴 저항 이동 필요 |

**RTU 프로젝트**: Full-auto 모드 사용 (기본 설정, 별도 조작 불필요)

## 4. RS485 채널 ↔ UART 매핑

```
┌──────────────────────────────────────────────────────────┐
│              CM4-ETH-RS485-BASE-B                        │
│                                                          │
│  ┌──────┐    ┌──────────┐    ┌────────────┐              │
│  │ CM4  │    │ 디지털   │    │ SP3485     │  [COM0] A,B  │
│  │      ├────┤ 아이솔   ├────┤ 트랜시버   ├──→ 디버거    │
│  │GPIO  │    │ 레이터   │    │ (절연)     │              │
│  │14/15 │    └──────────┘    └────────────┘              │
│  │      │                                                │
│  │GPIO  ├────[아이솔]────[SP3485]────[COM1] A,B → 설비   │
│  │ 4/5  │                                                │
│  │      │                                                │
│  │GPIO  ├────[아이솔]────[SP3485]────[COM2] A,B → DER-AVM│
│  │ 8/9  │                                                │
│  │      │                                                │
│  │GPIO  ├────[아이솔]────[SP3485]────[COM3] A,B → 예비   │
│  │12/13 │                                                │
│  └──────┘                                                │
│                                                          │
│  [ETH0] ─── Gigabit (CM4 네이티브)                       │
│  [ETH1] ─── 100Mbps (USB 확장)                           │
│  [USB]  ─── USB 2.0 x 2                                  │
│  [PWR]  ─── 7~36V DC / 5V USB-C                          │
└──────────────────────────────────────────────────────────┘
```

| COM 채널 | UART | GPIO TX/RX | 리눅스 디바이스 | 오버레이 | RTU 용도 |
|---------|------|-----------|---------------|---------|---------|
| COM0 | UART0 | GPIO 14/15 | /dev/ttyAMA0 | dtoverlay=uart0 | 디버거 |
| COM1 | UART3 | GPIO 4/5 | /dev/ttyAMA3 | dtoverlay=uart3 | 설비 통신 |
| COM2 | UART4 | GPIO 8/9 | /dev/ttyAMA4 | dtoverlay=uart4 | DER-AVM |
| COM3 | UART5 | GPIO 12/13 | /dev/ttyAMA5 | dtoverlay=uart5 | 예비 |

**주의**: OS 버전에 따라 `/dev/ttyAMA*` 번호가 다를 수 있음 (05_CM4_부팅_설정.md 참조)

## 5. config.txt 필수 설정

```ini
# RS485 4채널 UART 활성화
dtoverlay=uart0
dtoverlay=uart3
dtoverlay=uart4
dtoverlay=uart5
dtoverlay=disable-bt
enable_uart=1

# RTC 활성화 (선택)
dtoverlay=i2c-rtc,pcf85063a,i2c_csi_dsi
```

## 6. 기타 온보드 장치

| 장치 | GPIO/주소 | 용도 |
|------|----------|------|
| RTC (PCF85063) | I2C 0x51 | 실시간 시계 (정전 대비) |
| ATSHA204 | I2C | 보안 인증 (미사용) |
| 부저 | GPIO 22 | 알람 |
| 사용자 LED 1 | GPIO 20 | 상태 표시 |
| 사용자 LED 2 | GPIO 26 | 상태 표시 |
| 팬 | GPIO 18 (PWM) | 온도 제어 |

## 7. 유사 제품 비교

| 제품 | RS485 채널 | 이더넷 | 절연 | 가격 |
|------|-----------|--------|------|------|
| **CM4-ETH-RS485-BASE-B** | 4 | 1G + 100M | O | $58 |
| Waveshare 2-CH RS485 HAT | 2 (SPI 브리지) | 없음 | O | $20 |
| CM4-IO Pro (VK Engineering) | 1 + CAN | 1G | O | $80+ |
| RAK7391 (RAKwireless) | 모듈식 | 1G | - | $100+ |

## 8. 참고 링크

| 자료 | URL |
|------|-----|
| 제품 페이지 | https://www.waveshare.com/cm4-eth-rs485-base-b.htm |
| Wiki/문서 | https://www.waveshare.com/wiki/CM4-ETH-RS485-BASE-B |
| 회로도 PDF | https://files.waveshare.com/upload/c/c2/CM4-ETH-RS485-BASE-B_Part.pdf |
| 박스 버전 | https://www.waveshare.com/cm4-eth-rs485-box-b.htm |
| SP3485 데이터시트 | https://www.maxlinear.com/ds/sp3485.pdf |
| SpotPear 사용 가이드 | https://spotpear.com/index/study/detail/id/660.html |
| Amazon | https://www.amazon.com/Waveshare-Raspberry-Compute-Ethernet-Isolated/dp/B09V2KWRCS |
| CNX Software 리뷰 | https://www.cnx-software.com/2021/03/01/raspberry-pi-cm4-rs485-modbus-can-1-wire-interfaces/ |
