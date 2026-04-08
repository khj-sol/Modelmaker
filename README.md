# Model Maker

태양광 인버터 Modbus 매뉴얼 PDF를 분석해서 레지스터 맵을 추출하는 FastAPI 기반 웹 애플리케이션이다.

## 개요

이 프로젝트는 PDF 매뉴얼에서 레지스터 표가 있을 가능성이 높은 페이지를 찾아 이미지로 렌더링하고, Hugging Face의 `google/gemma-4-E4B` 비전 모델로 표를 JSON 배열로 추출한다. 추출 결과는 브라우저에서 표와 raw JSON으로 검토할 수 있고, 같은 결과를 Excel 파일로도 저장한다.

1단계는 PDF 기반 레지스터 표 추출이고, 2단계는 `RTU_UDP_Systems/common/*_registers.py`와 definitions JSON을 기준으로 H01 매핑과 `READ_BLOCKS`를 확인하는 단계다. 3단계는 대응되는 RTU 레지스터 모듈을 최종 `Register.py` 산출물로 생성하고 다운로드하는 단계다.

`example/output/*.py`는 매우 중요하다. 이 파일들은 단순 샘플이 아니라 few-shot 프롬프트를 구성하는 레퍼런스 데이터 소스다. 따라서 삭제 대상이 아니다.

## 주요 기능

- PDF 업로드
- 1단계 직접 실행 버튼
- 2단계 직접 실행 버튼
- PyMuPDF 기반 후보 페이지 탐색 및 고해상도 렌더링
- `example/output` 기반 few-shot 프롬프트 생성
- Gemma 비전 모델을 통한 레지스터 맵 JSON 추출
- 추출 결과 테이블 검증
- `RTU_UDP_Systems` 기준 H01 매핑 및 `READ_BLOCKS` 조회
- 최종 `Register.py` 코드 미리보기 및 다운로드
- Excel 다운로드

## 프로젝트 구조

```text
.
├─ app.py
├─ index.html
├─ gemini.md
├─ example/
│  ├─ input/
│  │  └─ *.pdf
│  └─ output/
│     ├─ Huawei_PV_50kw_registers.py
│     ├─ Kstar_PV_60kw_registers.py
│     └─ Senergy_PV_50kw_registers.py
└─ uploads/
```

## 동작 방식

1. 사용자가 PDF를 업로드한다.
2. 백엔드는 PDF 전체 페이지를 훑어 `register`, `modbus`, `address`, `0x` 같은 힌트로 점수를 계산한다.
3. 점수가 높은 최대 3개 페이지를 선택해 이미지로 렌더링한다.
4. `example/output/*.py`에서 추출한 레퍼런스 행을 few-shot 예시로 프롬프트에 포함한다.
5. `google/gemma-4-E4B`가 페이지 이미지를 바탕으로 JSON 배열을 반환한다.
6. 백엔드는 JSON을 정규화한 뒤 프론트엔드 표와 Excel 파일로 제공한다.
7. 사용자가 2단계를 실행하면 업로드한 PDF 이름을 기준으로 `RTU_UDP_Systems/common/*_registers.py`를 찾고, 해당 모듈의 `DATA_PARSER`, `READ_BLOCKS`, `MPPT_CHANNELS`, `STRING_CHANNELS`를 읽어 결과와 Excel 파일을 만든다.
8. 사용자가 3단계를 실행하면 같은 RTU 레지스터 모듈을 기준으로 최종 `Register.py` 파일을 생성해 코드 미리보기와 다운로드를 제공한다.

## 요구 환경

- Python 3.12 권장
- FastAPI
- Uvicorn
- PyMuPDF
- Pandas
- Pillow
- Transformers
- Torch
- Hugging Face 모델 다운로드가 가능한 환경

## Windows 설치

Windows에서는 먼저 전용 가상환경을 만들어야 한다.

```bat
setup_windows_env.bat
```

이 배치 파일은 아래 작업을 수행한다.

- `py -3 -m venv venv`로 Windows 가상환경 생성
- `pip` 업그레이드
- 필요한 패키지 설치

수동 설치가 필요하면 아래 명령을 사용한다.

```bat
py -3 -m venv venv
call venv\Scripts\activate.bat
pip install --upgrade pip
pip install fastapi uvicorn pymupdf pandas pillow transformers torch openpyxl python-multipart
```

## 실행

Windows에서는 아래 배치 파일로 실행한다.

```bat
run_model_maker.bat
```

이 배치 파일은 `venv\Scripts\python.exe`가 있을 때만 실행된다.

실행 후 기본 브라우저에서 `http://127.0.0.1:8000`이 자동으로 열린다.

## API

### `GET /api/config`

현재 모델 ID, 장치 정보, 레퍼런스 컬럼 수, 예제 입력 파일 목록을 반환한다.

### `POST /api/upload`

PDF를 업로드한다.

폼 필드:

- `file`: PDF 파일

### `POST /api/step1`

1단계 파싱을 실행한다.

폼 필드:

- `filename`: 업로드된 PDF 파일명

응답:

- 분석 페이지 번호
- 정규화된 표 데이터
- raw JSON
- 프롬프트 미리보기
- 페이지 썸네일
- Excel 다운로드 URL
- 경고 메시지

### `POST /api/step2`

2단계 RTU UDP 매핑을 실행한다.

폼 필드:

- `filename`: 업로드된 PDF 파일명

응답:

- 매칭된 RTU 모듈명과 파일 경로
- 제조사명
- `MPPT_CHANNELS`, `STRING_CHANNELS`
- H01 매핑 목록
- `READ_BLOCKS`
- 상태 코드 definitions
- Excel 다운로드 URL
- 경고 메시지

### `POST /api/step3`

3단계 `Register.py` 생성을 실행한다.

폼 필드:

- `filename`: 업로드된 PDF 파일명

응답:

- 매칭된 RTU 모듈명과 원본 경로
- 생성된 `Register.py` 파일명과 저장 경로
- 전체 코드 미리보기
- 다운로드 URL
- 경고 메시지

### `GET /download/{filename}`

생성된 Excel 파일을 다운로드한다.

## 주의사항

- 현재 구현 범위는 1단계, 2단계, 3단계다.
- 모델 가중치 다운로드와 Torch 환경이 정상이어야 실제 추론이 동작한다.
- 이 저장소에서 유지해야 하는 Python 파일은 `app.py`, `example/output/*.py`, `RTU_UDP_Systems/common/*_registers.py`다.
- `__pycache__`와 `.pyc`는 재생성 가능한 산출물이므로 삭제해도 된다.

## 정리 기준

삭제하지 않는 파일:

- `app.py`
- `example/output/*.py`
- `RTU_UDP_Systems/common/*_registers.py`

삭제 가능한 파일:

- `__pycache__/`
- `*.pyc`
