# Model Maker

Modbus 매뉴얼 PDF를 분석해 레지스터 정보를 추출하고, RTU 매핑/최종 Register.py 생성까지 지원하는 FastAPI 기반 웹 도구입니다.

## 주요 기능

- PDF 업로드
- Step 1: 레지스터 표 추출 + JSON/테이블 확인 + Excel 다운로드
- Step 2: `RTU_UDP_Systems/common/*_registers.py` 기반 H01 매핑/`READ_BLOCKS` 확인 + Excel 다운로드
- Step 3: 매핑된 RTU 모듈 기반 최종 `Register.py` 생성 + 다운로드

## 프로젝트 구조

```text
.
├─ app.py
├─ index.html
├─ README.md
├─ setup_windows_env.bat
├─ run_model_maker.bat
├─ example/
│  ├─ input/
│  └─ output/
├─ uploads/           # 런타임 업로드 임시 폴더(자동 생성)
├─ output/            # 런타임 산출물 폴더(자동 생성)
└─ RTU_UDP_Systems/   # 로컬 참조 데이터(현재 git ignore)
```

## 요구 사항

- Windows
- Python 3.10+
- 인터넷 연결 (최초 모델/패키지 다운로드 시)

## 설치 (Windows)

```bat
setup_windows_env.bat
```

위 스크립트가 수행하는 작업:

- `venv` 가상환경 생성
- `pip` 업그레이드
- CUDA 12.8용 PyTorch 설치 시도:
  - `pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision torchaudio`
  - 실패 시 기본 PyTorch 휠로 자동 fallback
- 앱 의존성 설치:
  - `fastapi uvicorn pymupdf pandas pillow "transformers>=4.57.0" accelerate openpyxl python-multipart sentencepiece protobuf safetensors`

## 실행

```bat
run_model_maker.bat
```

- 서버: `uvicorn app:app --host 0.0.0.0 --port 8000 --reload`
- 브라우저 자동 오픈: `http://127.0.0.1:8000`

## 동작 방식 요약

1. 업로드된 PDF를 `uploads/`에 저장
2. Step 1에서 PDF 전 페이지를 스캔해 후보를 찾고, 최대 4페이지를 우선 분석
3. `example/output/*.py`를 few-shot 참조로 활용
4. 필요 시 비전 모델(`google/gemma-4-E4B-it`) 기반 JSON 추출 fallback 사용
5. Step 2에서 RTU 모듈(`*_registers.py`)과 definitions JSON으로 H01/READ_BLOCKS 확인
6. Step 3에서 최종 Register.py 파일을 `output/`에 생성

## API

- `GET /api/config`
- `POST /api/upload`
  - form-data: `file` (PDF)
- `POST /api/step1`
  - form-data: `filename`
- `POST /api/step2`
  - form-data: `filename`
- `POST /api/step3`
  - form-data: `filename`
- `GET /download/{filename}`

## Git 관리 메모

다음 항목은 런타임/로컬 산출물이라 `.gitignore`로 제외되어 있습니다.

- `venv/`, `venv_bad_backup/`
- `uploads/`, `output/`
- `.hf-cache/`
- `RTU_UDP_Systems/`
- `__pycache__/`, `*.pyc`
