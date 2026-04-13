# HW 개발 레퍼런스 자료

CM4-ETH-RS485-BASE-B 보드 및 Raspberry Pi CM4 관련 하드웨어 자료 모음입니다.

## 문서 목록

| # | 파일 | 내용 |
|---|------|------|
| 01 | [CM4 모듈 스펙](01_CM4_모듈_스펙.md) | CPU, RAM, eMMC, 전기적 특성, 공식 PDF 링크 |
| 02 | [CM4 핀맵 GPIO](02_CM4_핀맵_GPIO.md) | UART/SPI/I2C GPIO 매핑, 핀 충돌 표, 이더넷 |
| 03 | [CM4-ETH-RS485 보드](03_CM4_ETH_RS485_보드.md) | 보드 스펙, RS485 트랜시버, 채널-UART 매핑, 회로도 |
| 04 | [RS485 통신 규격](04_RS485_통신_규격.md) | 전기 규격, Modbus RTU 프레임, 배선 권장사항 |
| 05 | [CM4 부팅 설정](05_CM4_부팅_설정.md) | config.txt, Device Tree Overlay, 시리얼 콘솔 |
| 06 | [참고 링크 모음](06_참고_링크_모음.md) | 전체 PDF/데이터시트 다운로드 링크 |

## RS485 채널 할당 (RTU 프로젝트)

```
COM0 (UART0, GPIO 14/15) ── 디버거 (시리얼 콘솔)
COM1 (UART3, GPIO  4/ 5) ── 설비 통신 (인버터, 릴레이, 기상센서)
COM2 (UART4, GPIO  8/ 9) ── DER-AVM 마스터 연결
COM3 (UART5, GPIO 12/13) ── 예비
```

## 핵심 PDF 다운로드

| 문서 | 링크 |
|------|------|
| CM4 데이터시트 | https://datasheets.raspberrypi.com/cm4/cm4-datasheet.pdf |
| CM4-ETH-RS485-BASE-B 회로도 | https://files.waveshare.com/upload/c/c2/CM4-ETH-RS485-BASE-B_Part.pdf |
| SP3485 트랜시버 데이터시트 | https://www.maxlinear.com/ds/sp3485.pdf |
