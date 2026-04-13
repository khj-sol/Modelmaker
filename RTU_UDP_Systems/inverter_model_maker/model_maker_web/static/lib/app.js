// Model Maker Web v1.0.0 — React 18 CDN (React.createElement, no JSX)
'use strict';

const { useState, useEffect, useRef, useCallback, useMemo } = React;
const h = React.createElement;

// ---------------------------------------------------------------------------
// Constants & Helpers
// ---------------------------------------------------------------------------
const API = window.location.origin;

async function apiFetch(path, opts = {}) {
  const r = await fetch(API + path, opts);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const j = await r.json(); msg = j.detail || JSON.stringify(j) || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

async function apiGet(path) { return apiFetch(path); }
async function apiPost(path, body) {
  return apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
async function apiPut(path, body) {
  return apiFetch(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
async function apiDelete(path) {
  return apiFetch(path, { method: 'DELETE' });
}

// Session ID persisted in sessionStorage
function getSessionId() {
  let sid = sessionStorage.getItem('mm_session_id');
  if (!sid) {
    sid = crypto.randomUUID();
    sessionStorage.setItem('mm_session_id', sid);
  }
  return sid;
}

function cls(...args) { return args.filter(Boolean).join(' '); }

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Badge({ text, color = 'bg-blue-600' }) {
  return h('span', { className: cls('px-2.5 py-1 rounded-md text-xs font-bold', color) }, text);
}

function Spinner() {
  return h('svg', {
    className: 'animate-spin h-4 w-4 text-blue-400',
    fill: 'none', viewBox: '0 0 24 24',
    xmlns: 'http://www.w3.org/2000/svg',
  },
    h('circle', { className: 'opacity-25', cx: 12, cy: 12, r: 10, stroke: 'currentColor', strokeWidth: 4 }),
    h('path', { className: 'opacity-75', fill: 'currentColor', d: 'M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z' })
  );
}

function ProgressBar({ pct, label }) {
  const p = Math.max(0, Math.min(100, pct || 0));
  return h('div', { className: 'w-full' },
    label && h('div', { className: 'text-xs text-gray-300 mb-1 truncate' }, label),
    h('div', { className: 'w-full bg-gray-700 rounded-full h-2' },
      h('div', {
        className: 'progress-bar-inner bg-blue-500 h-2 rounded-full',
        style: { width: `${p}%` },
      })
    )
  );
}

function Alert({ type = 'info', children }) {
  const colors = {
    info: 'bg-blue-900/50 border-blue-700 text-blue-200',
    success: 'bg-green-900/50 border-green-700 text-green-200',
    error: 'bg-red-900/50 border-red-700 text-red-200',
    warning: 'bg-yellow-900/50 border-yellow-700 text-yellow-200',
  };
  return h('div', { className: cls('border rounded p-3 text-sm', colors[type] || colors.info) }, children);
}

function Button({ onClick, disabled, children, variant = 'primary', size = 'md', className = '' }) {
  const variants = {
    primary: 'bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white shadow-md shadow-blue-900/50 hover:shadow-lg hover:shadow-blue-800/50 disabled:bg-blue-900 disabled:text-blue-500 disabled:shadow-none',
    secondary: 'bg-gray-600 hover:bg-gray-500 active:bg-gray-700 text-white shadow-md shadow-gray-900/50 disabled:bg-gray-800 disabled:text-gray-500 disabled:shadow-none',
    success: 'bg-green-600 hover:bg-green-500 active:bg-green-700 text-white shadow-md shadow-green-900/50 hover:shadow-lg hover:shadow-green-800/50 disabled:bg-green-900 disabled:text-green-600 disabled:shadow-none',
    danger: 'bg-red-600 hover:bg-red-500 active:bg-red-700 text-white shadow-md shadow-red-900/50 hover:shadow-lg hover:shadow-red-800/50 disabled:bg-red-900 disabled:text-red-600 disabled:shadow-none',
    outline: 'border-2 border-gray-500 hover:border-gray-400 hover:bg-gray-700 text-gray-200 disabled:opacity-40',
  };
  const sizes = { sm: 'px-3 py-1 text-xs', md: 'px-4 py-2 text-sm', lg: 'px-5 py-2.5 text-base' };
  return h('button', {
    onClick,
    disabled,
    className: cls('rounded-md font-semibold transition-all duration-150 disabled:cursor-not-allowed flex items-center gap-1.5',
      variants[variant] || variants.primary, sizes[size] || sizes.md, className),
  }, children);
}

function Toggle({ label, checked, onChange }) {
  return h('label', { className: 'flex items-center gap-2 cursor-pointer' },
    h('div', { className: 'relative' },
      h('input', { type: 'checkbox', className: 'sr-only', checked, onChange }),
      h('div', { className: cls('w-10 h-5 rounded-full transition-colors', checked ? 'bg-blue-600' : 'bg-gray-600') }),
      h('div', {
        className: cls('absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform',
          checked ? 'translate-x-5' : 'translate-x-0'),
      })
    ),
    h('span', { className: 'text-sm text-gray-300' }, label)
  );
}

function AboutModal({ open, onClose }) {
  useEffect(() => {
    if (!open) return;
    const handler = e => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open]);

  if (!open) return null;

  const section = (title, items) => h('div', { className: 'space-y-1' },
    h('div', { className: 'text-xs font-bold text-blue-300 uppercase tracking-wider' }, title),
    items.map((item, i) => h('div', { key: i, className: 'text-sm text-gray-200 pl-2' }, item))
  );

  return h('div', {
    className: 'fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm',
    onClick: e => { if (e.target === e.currentTarget) onClose(); },
  },
    h('div', { className: 'bg-gray-800 border border-gray-600 rounded-xl shadow-2xl w-full max-w-lg mx-4 p-6 space-y-5' },
      // Header
      h('div', { className: 'flex items-center justify-between' },
        h('div', { className: 'flex items-center gap-3' },
          h('div', { className: 'text-2xl' }, '⚙️'),
          h('div', null,
            h('div', { className: 'text-lg font-bold text-white' }, 'Model Maker Web'),
            h('div', { className: 'text-xs text-gray-400' }, 'v1.0.0')
          )
        ),
        h('button', {
          onClick: onClose,
          className: 'text-gray-400 hover:text-white text-xl leading-none px-2 py-1 rounded hover:bg-gray-700 transition-colors',
        }, '\u2715')
      ),

      h('div', { className: 'text-sm text-gray-300 border-b border-gray-700 pb-4' },
        'RTU UDP System \uc778\ubc84\ud130 Modbus \ub808\uc9c0\uc2a4\ud130\ub9f5 \uc790\ub3d9 \uc0dd\uc131\uae30'),

      section('\uc8fc\uc694 \uae30\ub2a5', [
        '\u2022 Stage 1: PDF/Excel \u2192 \ub808\uc9c0\uc2a4\ud130 \ud14c\uc774\ube14 \ucd94\ucd9c',
        '\u2022 Stage 2: Solarize \ud45c\uc900 \ud504\ub85c\ud1a0\ucf5c \uc790\ub3d9 \ub9e4\ud551',
        '\u2022 Stage 3: RTU \ud638\ud658 Python \ub808\uc9c0\uc2a4\ud130\ub9f5 \ucf54\ub4dc \uc0dd\uc131 & 12\ud56d\ubaa9 \uac80\uc99d',
      ]),

      section('\uc2dc\uc2a4\ud15c', [
        '\u2022 RTU UDP System V1.1.0',
        '\u2022 Solarize Modbus Protocol V2.0.11',
        '\u2022 \uc9c0\uc6d0: Solarize, Huawei, Kstar, Sungrow, EKOS, Senergy, GoodWe',
      ]),

      section('\uae30\uc220 \uc2a4\ud0dd', [
        '\u2022 Backend: FastAPI + Python 3.12',
        '\u2022 Frontend: React 18 + Tailwind CSS (CDN)',
        '\u2022 PDF \ud30c\uc2f1: PyMuPDF (fitz)',
        '\u2022 AI \ubaa8\ub4dc: Claude API (Anthropic)',
      ]),

      h('div', { className: 'border-t border-gray-700 pt-4 text-xs text-gray-400' },
        h('span', null, '\u00a9 2026 Solarize Co., Ltd.')
      )
    )
  );
}

function LogPanel({ messages, placeholder = '\ub300\uae30 \uc911...', maxH = 'max-h-40' }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [messages]);
  return h('div', {
    ref,
    className: cls('overflow-y-auto bg-gray-800 rounded p-2 font-mono text-xs text-gray-300 space-y-0.5', maxH),
  },
    messages.length === 0
      ? h('div', { className: 'text-gray-400' }, placeholder)
      : messages.map((m, i) => h('div', { key: i, className: 'leading-relaxed' }, m))
  );
}

// ---------------------------------------------------------------------------
// Drop Zone for PDF Upload
// ---------------------------------------------------------------------------
function PDFDropZone({ sessionId, onUploaded }) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  async function uploadFile(file) {
    const ext = file ? file.name.toLowerCase().split('.').pop() : '';
    if (!file || !['pdf', 'xlsx', 'xls'].includes(ext)) {
      setError('PDF 또는 Excel(.xlsx) 파일만 업로드 가능합니다.');
      return;
    }
    setError('');
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch(`${API}/api/upload-pdf?session_id=${sessionId}`, {
        method: 'POST', body: fd,
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      const data = await r.json();
      onUploaded(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  }

  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  }

  function onInputChange(e) {
    const file = e.target.files[0];
    if (file) uploadFile(file);
    e.target.value = '';
  }

  return h('div', { className: 'space-y-2' },
    h('div', {
      className: cls('drop-zone rounded-lg p-8 text-center cursor-pointer', dragging && 'drag-over'),
      onDragOver: e => { e.preventDefault(); setDragging(true); },
      onDragLeave: () => setDragging(false),
      onDrop,
      onClick: () => inputRef.current?.click(),
    },
      uploading
        ? h('div', { className: 'flex flex-col items-center gap-2' },
            h(Spinner),
            h('div', { className: 'text-gray-300 text-sm' }, '업로드 중...')
          )
        : h('div', { className: 'flex flex-col items-center gap-2' },
            h('div', { className: 'text-4xl text-gray-400' }, '📄'),
            h('div', { className: 'text-gray-300 text-sm' }, 'PDF 또는 Excel 파일을 드래그하거나 클릭하여 선택'),
            h('div', { className: 'text-gray-500 text-xs' }, 'Modbus Protocol PDF / Excel (.xlsx)')
          )
    ),
    h('input', { ref: inputRef, type: 'file', accept: '.pdf,.xlsx,.xls', className: 'hidden', onChange: onInputChange }),
    error && h(Alert, { type: 'error' }, error)
  );
}

// ---------------------------------------------------------------------------
// Data Table (virtualized-style, fixed header)
// ---------------------------------------------------------------------------
function DataTable({ rows, columns, editMode = false, onCellEdit, maxH = 'max-h-96', colLabels = {} }) {
  const [editCell, setEditCell] = useState(null); // {row, col}
  const [editVal, setEditVal] = useState('');

  if (!rows || rows.length === 0) {
    return h('div', { className: 'text-gray-400 text-sm p-4 text-center' }, '데이터 없음');
  }

  const cols = columns || Object.keys(rows[0] || {});

  function startEdit(ri, col) {
    if (!editMode) return;
    setEditCell({ ri, col });
    setEditVal(rows[ri][col] || '');
  }

  async function commitEdit() {
    if (!editCell) return;
    const { ri, col } = editCell;
    if (onCellEdit) await onCellEdit(ri, col, editVal);
    setEditCell(null);
  }

  function onKeyDown(e) {
    if (e.key === 'Enter') commitEdit();
    if (e.key === 'Escape') setEditCell(null);
  }

  const MATCH_COLORS = {
    'Addr-Match': 'text-green-400',
    'Name-Match': 'text-blue-400',
    'Reference': 'text-purple-400',
    'Heuristic': 'text-yellow-400',
    'Supplement': 'text-orange-400',
    'Unmapped': 'text-red-400',
  };

  return h('div', { className: cls('overflow-auto rounded border border-gray-700', maxH) },
    h('table', { className: 'text-xs border-collapse' },
      h('thead', { className: 'sticky top-0 bg-gray-800 z-10' },
        h('tr', null,
          cols.map(col => h('th', {
            key: col,
            className: 'px-2 py-1.5 text-left text-gray-300 border-b border-gray-700 whitespace-nowrap',
          }, colLabels[col] || col))
        )
      ),
      h('tbody', null,
        rows.map((row, ri) => h('tr', {
          key: ri,
          className: cls('border-b border-gray-800 hover:bg-gray-800/50',
            ri % 2 === 0 ? 'bg-gray-900' : 'bg-gray-900/50'),
        },
          cols.map(col => {
            const val = row[col] || '';
            const isEditing = editMode && editCell && editCell.ri === ri && editCell.col === col;
            const matchColor = (col === 'Match Type' || col === 'Match_Type') ? (
              MATCH_COLORS[val] || (val.startsWith && val.startsWith('Ref(') ? 'text-purple-400' :
              val.startsWith && val.startsWith('Name(') ? 'text-blue-400' :
              val.startsWith && val.startsWith('Addr(') ? 'text-green-400' : 'text-gray-300')
            ) : '';
            return h('td', {
              key: col,
              className: cls('px-2 py-1 max-w-xs truncate', matchColor,
                editMode && 'cursor-pointer hover:bg-blue-900/30'),
              onClick: () => startEdit(ri, col),
              title: val,
            },
              isEditing
                ? h('input', {
                    type: 'text',
                    value: editVal,
                    onChange: e => setEditVal(e.target.value),
                    onBlur: commitEdit,
                    onKeyDown,
                    autoFocus: true,
                    className: 'cell-edit w-full bg-gray-800 border border-blue-500 rounded px-1 text-gray-100',
                  })
                : val
            );
          })
        ))
      )
    )
  );
}

// ---------------------------------------------------------------------------
// Stage 1 Tab
// ---------------------------------------------------------------------------
function Stage1Tab({ sessionId, pdfInfo, onPdfUploaded, onDetected, wsMessages }) {
  const [mode, setMode] = useState('offline');
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const [rows, setRows] = useState([]);
  const [logs, setLogs] = useState([]);

  // Filter ws messages for stage1
  useEffect(() => {
    const msgs = wsMessages.filter(m => m.stage === 'stage1');
    if (msgs.length === 0) return;
    const newLogs = msgs.map(m => {
      if (m.type === 'progress') return `[진행] ${m.message}`;
      if (m.type === 'done') return m.success ? `✓ ${m.detail}` : `✗ ${m.detail}`;
      if (m.type === 'error') return `✗ 오류: ${m.error}`;
      return '';
    }).filter(Boolean);
    if (newLogs.length > 0) setLogs(prev => [...prev, ...newLogs]);

    const last = msgs[msgs.length - 1];
    if (last.type === 'done') {
      setRunning(false);
      if (last.success) {
        setDone(true);
        loadResult();
      } else {
        setError(last.detail || '실패');
      }
    } else if (last.type === 'error') {
      setRunning(false);
      setError(last.error);
    }
  }, [wsMessages]);

  // Map API keys to UI column names
  const S1_KEY_MAP = {
    'No': 'No', 'Section': 'Section',
    'Address_Hex': 'Addr(Hex)', 'Address_Dec': 'Addr(Dec)',
    'Definition': 'Definition', 'Data_Type': 'Data Type',
    'FC_Code': 'FC', 'Registers': 'Regs',
    'Unit': 'Unit', 'Scale_Factor': 'Scale',
    'R/W': 'R/W', 'Description': 'Comment',
  };

  async function loadResult() {
    try {
      const data = await apiGet(`/api/stage1/result?session_id=${sessionId}`);
      const mapped = (data.rows || []).map(row => {
        const out = {};
        for (const [apiKey, uiCol] of Object.entries(S1_KEY_MAP)) {
          const v = row[apiKey];
          out[uiCol] = v !== null && v !== undefined ? String(v) : '';
        }
        return out;
      });
      setRows(mapped);
      // Pass auto-detected counts to parent
      if (data.detected && onDetected) {
        onDetected(data.detected);
      }
    } catch (e) {
      setError(e.message);
    }
  }

  async function runStage1() {
    if (!pdfInfo) { setError('먼저 PDF를 업로드하세요.'); return; }
    setRunning(true);
    setDone(false);
    setError('');
    setLogs([`[시작] PDF: ${pdfInfo.filename}, 모드: ${mode}`]);
    try {
      await apiPost('/api/stage1/run', { session_id: sessionId, mode });
    } catch (e) {
      setRunning(false);
      setError(e.message);
    }
  }

  const COLS = ['No', 'Section', 'Addr(Hex)', 'Addr(Dec)', 'Definition', 'Data Type',
                'FC', 'Regs', 'Unit', 'Scale', 'R/W', 'Comment'];

  return h('div', { className: 'space-y-4' },
    h('div', { className: 'bg-gray-800 rounded-lg p-4 space-y-4' },
      h('h2', { className: 'text-base font-semibold text-gray-200' }, 'Stage 1 — PDF/Excel → 레지스터 추출'),

      h(Alert, { type: 'info' },
        'AI 모드는 Claude API를 사용하여 PDF 파싱 정확도를 향상시킵니다. API 키가 없으면 오프라인 모드만 사용 가능합니다. (관리 탭 > ai_settings.ini)'
      ),

      !pdfInfo && h(PDFDropZone, { sessionId, onUploaded: onPdfUploaded }),

      pdfInfo && h(Alert, { type: 'success' },
        `✓ 업로드됨: ${pdfInfo.filename} (${(pdfInfo.size / 1024).toFixed(1)} KB)`
      ),

      h('div', { className: 'flex items-center gap-4 flex-wrap' },
        h('div', { className: 'flex items-center gap-2' },
          h('span', { className: 'text-sm text-gray-300' }, '모드:'),
          h('div', { className: 'flex rounded overflow-hidden border border-gray-600' },
            ['offline', 'ai'].map(m =>
              h('button', {
                key: m,
                onClick: () => setMode(m),
                className: cls('px-3 py-1 text-sm transition-colors',
                  mode === m ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'),
              }, m === 'offline' ? '자동매핑(Offline)' : 'AI 매핑(Claude)')
            )
          )
        ),

        h(Button, {
          onClick: runStage1,
          disabled: running || !pdfInfo,
          variant: 'primary',
        },
          running ? h(Spinner) : null,
          running ? '실행 중...' : '추출 실행'
        ),

        done && rows.length > 0 && h('a', {
          href: `/api/stage1/download-excel?session_id=${sessionId}`,
          className: 'px-4 py-2 text-sm rounded-md font-semibold bg-green-600 hover:bg-green-500 active:bg-green-700 text-white shadow-md shadow-green-900/50 transition-all duration-150',
        }, '📥 Excel 다운로드'),
      ),

      error && h(Alert, { type: 'error' }, error),

      h('div', { className: 'space-y-1' },
        h('div', { className: 'text-xs text-gray-400 font-medium' }, '진행 로그'),
        h(LogPanel, { messages: logs, placeholder: '모드를 선택하고 추출 실행하세요.' })
      ),
    ),

    done && rows.length > 0 && h('div', { className: 'bg-gray-800 rounded-lg p-4 space-y-2' },
      h('div', { className: 'flex items-center justify-between' },
        h('h3', { className: 'text-sm font-semibold text-gray-300' },
          `추출 결과 (${rows.length}개 레지스터)`),
      ),
      h(DataTable, { rows, columns: COLS, maxH: 'max-h-80' }),
      h(Alert, { type: 'success' }, '✓ Stage 1 완료 — Stage 2 탭으로 이동하여 자동 매핑을 실행하세요.')
    )
  );
}

// ---------------------------------------------------------------------------
// Stage 2 Tab
// ---------------------------------------------------------------------------
function Stage2Tab({ sessionId, stage1Done, detected, wsMessages }) {
  const [mode, setMode] = useState('offline');
  const [mppt, setMppt] = useState(detected?.mppt_count || 4);
  const [strings, setStrings] = useState(detected?.string_count || 8);

  // Update when detected values change (after Stage 1)
  useEffect(() => {
    if (detected) {
      setMppt(detected.mppt_count || 4);
      setStrings(detected.string_count || 8);
    }
  }, [detected?.mppt_count, detected?.string_count]);
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const [rows, setRows] = useState([]);
  const [logs, setLogs] = useState([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const msgs = wsMessages.filter(m => m.stage === 'stage2');
    if (msgs.length === 0) return;
    const newLogs = msgs.map(m => {
      if (m.type === 'progress') return `[진행] ${m.message}`;
      if (m.type === 'done') return m.success ? `✓ ${m.detail}` : `✗ ${m.detail}`;
      if (m.type === 'error') return `✗ 오류: ${m.error}`;
      return '';
    }).filter(Boolean);
    if (newLogs.length > 0) setLogs(prev => [...prev, ...newLogs]);
    const last = msgs[msgs.length - 1];
    if (last.type === 'done') {
      setRunning(false);
      if (last.success) { setDone(true); loadResult(); }
      else setError(last.detail || '실패');
    } else if (last.type === 'error') {
      setRunning(false);
      setError(last.error);
    }
  }, [wsMessages]);

  async function loadResult() {
    try {
      const data = await apiGet(`/api/stage2/result?session_id=${sessionId}`);
      setRows(data.rows || []);
    } catch (e) {}
  }

  async function runStage2() {
    setRunning(true); setDone(false); setError('');
    setLogs([`[시작] 자동 매핑, 모드: ${mode}`]);
    try {
      await apiPost('/api/stage2/run', {
        session_id: sessionId, mode, mppt_count: mppt, string_count: strings,
      });
    } catch (e) {
      setRunning(false);
      setError(e.message);
    }
  }

  async function onCellEdit(ri, col, val) {
    setSaving(true);
    try {
      await apiPut('/api/stage2/update-row', {
        session_id: sessionId, row_index: ri, col_name: col, value: val,
      });
      setRows(prev => {
        const next = [...prev];
        next[ri] = { ...next[ri], [col]: val };
        return next;
      });
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  const COLS = ['Section', 'Source_Addr_Hex', 'Source_Addr_Dec', 'Source_Name', 'Source_Type',
                'Source_Unit', 'Source_Scale', 'Source_RW', 'Source_Regs',
                '\u2192', 'Solarize_Name', 'Solarize_Addr_Hex', 'Match_Type', 'Notes'];
  const COL_LABELS = {
    'Source_Addr_Hex': 'Src Addr(Hex)', 'Source_Addr_Dec': 'Src Addr(Dec)',
    'Source_Name': 'Src Name', 'Source_Type': 'Data Type',
    'Source_Unit': 'Unit', 'Source_Scale': 'Scale', 'Source_RW': 'R/W',
    'Source_Regs': 'Regs', '\u2192': '\u2192', 'Solarize_Name': 'Sol Name',
    'Solarize_Addr_Hex': 'Sol Addr', 'Match_Type': 'Match Type',
  };

  return h('div', { className: 'space-y-4' },
    h('div', { className: 'bg-gray-800 rounded-lg p-4 space-y-4' },
      h('h2', { className: 'text-base font-semibold text-gray-200' }, 'Stage 2 — 자동 매핑'),

      !stage1Done && h(Alert, { type: 'warning' }, 'Stage 1을 먼저 완료하세요.'),

      h('div', { className: 'flex items-center gap-4 flex-wrap' },
        h('div', { className: 'flex items-center gap-2' },
          h('span', { className: 'text-sm text-gray-300' }, '모드:'),
          h('div', { className: 'flex rounded overflow-hidden border border-gray-600' },
            ['offline', 'ai'].map(m =>
              h('button', {
                key: m,
                onClick: () => setMode(m),
                className: cls('px-3 py-1 text-sm transition-colors',
                  mode === m ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'),
              }, m === 'offline' ? '자동매핑(Offline)' : 'AI 매핑(Claude)')
            )
          )
        ),

        h('div', { className: 'flex items-center gap-2' },
          h('span', { className: 'text-sm text-gray-300' }, 'MPPT:'),
          h('input', {
            type: 'number', min: 1, max: 9, value: mppt,
            onChange: e => setMppt(parseInt(e.target.value) || 4),
            className: 'w-16 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-center',
          })
        ),

        h('div', { className: 'flex items-center gap-2' },
          h('span', { className: 'text-sm text-gray-300' }, '스트링:'),
          h('input', {
            type: 'number', min: 1, max: 24, value: strings,
            onChange: e => setStrings(parseInt(e.target.value) || 8),
            className: 'w-16 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-center',
          })
        ),

        h(Button, {
          onClick: runStage2,
          disabled: running || !stage1Done,
          variant: 'primary',
        },
          running ? h(Spinner) : null,
          running ? '매핑 중...' : '자동 매핑 실행'
        ),

        done && rows.length > 0 && h('a', {
          href: `/api/stage2/download-excel?session_id=${sessionId}`,
          className: 'px-4 py-2 text-sm rounded-md font-semibold bg-green-600 hover:bg-green-500 active:bg-green-700 text-white shadow-md shadow-green-900/50 transition-all duration-150',
        }, '📥 Excel 다운로드'),
      ),

      error && h(Alert, { type: 'error' }, error),
      saving && h('div', { className: 'text-xs text-blue-400' }, '저장 중...'),

      h('div', { className: 'space-y-1' },
        h('div', { className: 'text-xs text-gray-400 font-medium' }, '진행 로그'),
        h(LogPanel, { messages: logs, placeholder: '자동 매핑 실행 버튼을 누르세요.' })
      ),
    ),

    done && rows.length > 0 && h('div', { className: 'bg-gray-800 rounded-lg p-4 space-y-2' },
      h('div', { className: 'flex items-center justify-between' },
        h('h3', { className: 'text-sm font-semibold text-gray-300' },
          `매핑 결과 (${rows.length}개 레지스터) — 셀 클릭으로 편집 가능`),
      ),
      h('div', { className: 'flex gap-2 flex-wrap text-xs mb-2' },
        [
          ['Ref(%)', 'text-purple-400', '레퍼런스 매핑 (유사도%)'],
          ['Unmapped', 'text-red-400', '미매핑'],
        ].map(([k, c, desc]) =>
          h('span', { key: k, className: cls(c) }, `■ ${k} (${desc})`)
        )
      ),
      h(DataTable, { rows, columns: COLS, editMode: true, onCellEdit, maxH: 'max-h-96', colLabels: COL_LABELS }),
      h(Alert, { type: 'success' }, '✓ Stage 2 완료 — Stage 3 탭으로 이동하여 코드 생성을 실행하세요.')
    )
  );
}

// ---------------------------------------------------------------------------
// Stage 3 Tab
// ---------------------------------------------------------------------------
function Stage3Tab({ sessionId, stage2Done, detected, wsMessages }) {
  const [mode, setMode] = useState('offline');
  const [protocol, setProtocol] = useState('custom');
  const [manufacturer, setManufacturer] = useState('');
  const [mppt, setMppt] = useState(detected?.mppt_count || 4);
  const [strings, setStrings] = useState(detected?.string_count || 8);
  const [ivScan, setIvScan] = useState(detected?.iv_scan || false);
  const [derAvm, setDerAvm] = useState(true);
  const deaAvm = derAvm;

  useEffect(() => {
    if (detected) {
      setMppt(detected.mppt_count || 4);
      setStrings(detected.string_count || 8);
      setIvScan(detected.iv_scan || false);
      if (detected.fc_code) setFcCode(detected.fc_code);
      if (detected.manufacturer) {
        setManufacturer(detected.manufacturer);
        setProtocol(detected.manufacturer.toLowerCase().replace(/[\s.]+/g, ''));
      }
    }
  }, [detected?.mppt_count, detected?.string_count, detected?.iv_scan, detected?.manufacturer]);
  const [fcCode, setFcCode] = useState('FC03');
  const [capacity, setCapacity] = useState('');
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const [code, setCode] = useState('');
  const [results, setResults] = useState([]);
  const [success, setSuccess] = useState(false);
  const [logs, setLogs] = useState([]);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const [showCode, setShowCode] = useState(false);

  useEffect(() => {
    const msgs = wsMessages.filter(m => m.stage === 'stage3');
    if (msgs.length === 0) return;
    const newLogs = msgs.map(m => {
      if (m.type === 'progress') return `[진행] ${m.message}`;
      if (m.type === 'done') return m.success ? `✓ ${m.detail}` : `✗ ${m.detail}`;
      if (m.type === 'error') return `✗ 오류: ${m.error}`;
      return '';
    }).filter(Boolean);
    if (newLogs.length > 0) setLogs(prev => [...prev, ...newLogs]);
    const last = msgs[msgs.length - 1];
    if (last.type === 'done') {
      setRunning(false);
      if (last.success !== undefined) {
        setDone(true);
        loadResult();
      }
    } else if (last.type === 'error') {
      setRunning(false);
      setError(last.error);
    }
  }, [wsMessages]);

  async function loadResult() {
    try {
      const data = await apiGet(`/api/stage3/result?session_id=${sessionId}`);
      setCode(data.code || '');
      setResults(data.results || []);
      setSuccess(data.success || false);
      setDone(true);
    } catch (e) {}
  }

  async function runStage3() {
    if (!protocol.trim()) { setError('프로토콜 이름을 입력하세요.'); return; }
    setRunning(true); setDone(false); setError(''); setSaveMsg('');
    setLogs([`[시작] 코드 생성 — protocol: ${protocol}, 모드: ${mode}`]);
    try {
      await apiPost('/api/stage3/run', {
        session_id: sessionId, mode,
        protocol_name: protocol.trim(),
        manufacturer,
        mppt_count: mppt,
        string_count: strings,
        iv_scan: ivScan,
        der_avm: derAvm,
        dea_avm: deaAvm,
        class_name: 'RegisterMap',
        fc_code: fcCode,
      });
    } catch (e) {
      setRunning(false);
      setError(e.message);
    }
  }

  async function saveOnly() {
    if (!protocol.trim() || !manufacturer.trim()) {
      setSaveMsg('✗ 프로토콜 이름과 제조사 이름을 모두 입력하세요.');
      return;
    }
    setSaving(true); setSaveMsg('');
    try {
      const desc = `${manufacturer.trim()} ${capacity.trim() || ''} 신규생성`.trim();
      const data = await apiPost('/api/stage3/save', {
        session_id: sessionId,
        protocol_name: protocol.trim(),
        save_as_reference: true,
        manufacturer: manufacturer.trim(),
        description: desc,
        capacity: capacity.trim(),
        mppt_count: mppt,
        string_count: strings,
        fc_code: fcCode,
        iv_scan: ivScan,
        der_avm: derAvm,
      });
      setSaveMsg(`✓ 저장 완료: ${data.filename}`);
    } catch (e) {
      setSaveMsg(`✗ 오류: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  const passCount = results.filter(r => r[0] === 'PASS').length;
  const [validationOpen, setValidationOpen] = useState(false);
  // Auto-open when there are failures
  useEffect(() => {
    if (results.length > 0) setValidationOpen(!success);
  }, [results, success]);

  return h('div', { className: 'space-y-4' },
    h('div', { className: 'bg-gray-800 rounded-lg p-4 space-y-4' },
      h('h2', { className: 'text-base font-semibold text-gray-200' }, 'Stage 3 — 코드 생성 & 검증'),

      !stage2Done && h(Alert, { type: 'warning' }, 'Stage 2를 먼저 완료하세요.'),

      h('div', { className: 'grid grid-cols-1 sm:grid-cols-2 gap-3' },

        h('div', { className: 'space-y-1' },
          h('label', { className: 'text-xs text-gray-300' }, '프로토콜 이름 (protocol_name)'),
          h('input', {
            type: 'text', value: protocol,
            onChange: e => setProtocol(e.target.value),
            placeholder: 'newbrand',
            className: 'w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm',
          })
        ),

        h('div', { className: 'space-y-1' },
          h('label', { className: 'text-xs text-gray-300' }, '제조사 (manufacturer)'),
          h('input', {
            type: 'text', value: manufacturer,
            onChange: e => setManufacturer(e.target.value),
            placeholder: 'NewBrand Inc.',
            className: 'w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm',
          })
        ),

        h('div', { className: 'flex items-center gap-4 flex-wrap' },
          h('div', { className: 'space-y-1' },
            h('label', { className: 'text-xs text-gray-300' }, '용량'),
            h('input', {
              type: 'text', value: capacity,
              onChange: e => {
                const val = e.target.value;
                setCapacity(val);
                // Auto-update protocol name: manufacturer + capacity
                if (manufacturer) {
                  const base = manufacturer.toLowerCase().replace(/[\s.]+/g, '');
                  const cap = val.replace(/[^0-9]/g, '');
                  setProtocol(cap ? `${base}_${cap}kw` : base);
                }
              },
              placeholder: '100kW',
              className: 'w-24 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-center',
            })
          ),
          h('div', { className: 'space-y-1' },
            h('label', { className: 'text-xs text-gray-300' }, 'MPPT 수'),
            h('input', {
              type: 'number', min: 1, max: 12, value: mppt,
              onChange: e => setMppt(parseInt(e.target.value) || 4),
              className: 'w-20 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-center',
            })
          ),
          h('div', { className: 'space-y-1' },
            h('label', { className: 'text-xs text-gray-300' }, '스트링 수'),
            h('input', {
              type: 'number', min: 1, max: 48, value: strings,
              onChange: e => setStrings(parseInt(e.target.value) || 8),
              className: 'w-20 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-center',
            })
          ),
          h('div', { className: 'space-y-1' },
            h('label', { className: 'text-xs text-gray-300' }, 'FC 코드'),
            h('select', {
              value: fcCode, onChange: e => setFcCode(e.target.value),
              className: 'bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm',
            },
              h('option', { value: 'FC03' }, 'FC03'),
              h('option', { value: 'FC04' }, 'FC04')
            )
          )
        ),

        h('div', { className: 'flex flex-col gap-2' },
          h('div', { className: 'flex items-center gap-3' },
            h(Toggle, { label: 'IV Scan 지원', checked: ivScan, onChange: e => setIvScan(e.target.checked) }),
            ivScan && detected?.iv_data_points > 0 && h('span', { className: 'text-xs text-blue-400 bg-blue-900/30 px-2 py-0.5 rounded' },
              `${detected.iv_data_points} points/set`),
          ),
          h(Toggle, { label: 'DER-AVM 지원', checked: derAvm, onChange: e => setDerAvm(e.target.checked) }),
        ),
      ),

      h('div', { className: 'flex items-center gap-3 flex-wrap' },
        h('div', { className: 'flex items-center gap-2' },
          h('span', { className: 'text-sm text-gray-300' }, '모드:'),
          h('div', { className: 'flex rounded overflow-hidden border border-gray-600' },
            ['offline', 'ai'].map(m =>
              h('button', {
                key: m,
                onClick: () => setMode(m),
                className: cls('px-3 py-1 text-sm transition-colors',
                  mode === m ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'),
              }, m === 'offline' ? '오프라인' : 'AI (Claude + 자동재시도)')
            )
          )
        ),

        h(Button, {
          onClick: runStage3,
          disabled: running || !stage2Done,
          variant: 'primary',
        },
          running ? h(Spinner) : null,
          running ? '생성 중...' : '코드 생성 실행'
        ),
      ),

      error && h(Alert, { type: 'error' }, error),

      h('div', { className: 'space-y-1' },
        h('div', { className: 'text-xs text-gray-400 font-medium' }, '진행 로그'),
        h(LogPanel, { messages: logs, placeholder: '코드 생성 실행 버튼을 누르세요.' })
      ),
    ),

    done && h('div', { className: 'bg-gray-800 rounded-lg p-4 space-y-4' },

      // Validation Results
      h('div', { className: 'space-y-2' },
        h('div', {
          className: 'flex items-center justify-between cursor-pointer select-none',
          onClick: () => setValidationOpen(v => !v),
        },
          h('h3', { className: 'text-sm font-semibold text-gray-300 flex items-center gap-2' },
            h('span', { className: 'text-xs text-gray-400' }, validationOpen ? '▼' : '▶'),
            `검증 결과 (${results.length}항목)`
          ),
          h('div', { className: 'flex items-center gap-2' },
            h(Badge, {
              text: `${passCount}/${results.length} 통과`,
              color: success ? 'bg-green-700' : 'bg-red-700',
            }),
            success
              ? h(Badge, { text: '✓ 생성 성공', color: 'bg-green-700' })
              : h(Badge, { text: '✗ 검증 실패', color: 'bg-red-700' })
          )
        ),

        validationOpen && h('div', { className: 'grid grid-cols-1 gap-1' },
          results.map(([status, msg], i) =>
            h('div', {
              key: i,
              className: cls('flex items-start gap-2 px-3 py-1.5 rounded text-xs',
                status === 'PASS' ? 'bg-green-900/30 text-green-300' : 'bg-red-900/30 text-red-300'),
            },
              h('span', { className: 'font-bold w-8 shrink-0' }, status),
              h('span', { className: 'text-gray-200' }, msg)
            )
          )
        )
      ),

      // Action Buttons
      h('div', { className: 'flex items-center gap-3 flex-wrap' },
        h(Button, {
          onClick: saveOnly,
          disabled: saving || !code,
          variant: 'success',
        },
          saving ? h(Spinner) : '💾',
          saving ? '저장 중...' : '저장'
        ),

        h('button', {
          onClick: () => setShowCode(v => !v),
          className: 'px-4 py-2 text-sm rounded-md font-semibold bg-gray-600 hover:bg-gray-500 active:bg-gray-700 text-white shadow-md shadow-gray-900/50 transition-all duration-150',
        }, showCode ? '코드 숨기기' : '코드 보기'),
      ),

      success && !saveMsg && h(Alert, { type: 'success' },
        '✓ 검증 통과 — 저장 버튼을 눌러 레지스터맵을 저장하세요.'),

      saveMsg && h(Alert, { type: saveMsg.startsWith('✓') ? 'success' : 'error' }, saveMsg),

      saveMsg && saveMsg.startsWith('✓') && h(Alert, { type: 'info' },
        '관리 탭으로 이동하여 모델 등록 및 Config 파일을 설정하세요.'),

      showCode && code && h('div', { className: 'space-y-1' },
        h('div', { className: 'text-xs text-gray-400 font-medium' }, '생성된 코드'),
        h('pre', {
          className: 'bg-gray-900 border border-gray-700 rounded p-3 text-xs text-green-300 overflow-x-auto max-h-96 overflow-y-auto',
        }, code)
      ),
    )
  );
}

// ---------------------------------------------------------------------------
// Management Tab (References + AI Settings)
// ---------------------------------------------------------------------------
function ManagementTab({ sessionId }) {
  const [refs, setRefs] = useState([]);
  const [aiKey, setAiKey] = useState('');
  const [aiModel, setAiModel] = useState('claude-opus-4-6');
  const [aiKeySet, setAiKeySet] = useState(false);
  const [aiPreview, setAiPreview] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const [deleting, setDeleting] = useState('');

  // Config file management
  const [configFiles, setConfigFiles] = useState([]);
  const [selectedConfig, setSelectedConfig] = useState('');
  const [configContent, setConfigContent] = useState('');
  const [configSaving, setConfigSaving] = useState(false);
  const [configMsg, setConfigMsg] = useState('');
  const [configLoading, setConfigLoading] = useState(false);

  async function loadConfigList() {
    try {
      const data = await apiGet('/api/config/list');
      setConfigFiles(data.files || []);
    } catch (e) {}
  }

  async function loadConfigFile(name) {
    setSelectedConfig(name);
    setConfigMsg('');
    try {
      const data = await apiGet(`/api/config/${name}`);
      setConfigContent(data.content || '');
    } catch (e) {
      setConfigMsg(`✗ ${e.message}`);
    }
  }

  async function saveConfigFile() {
    if (!selectedConfig) return;
    setConfigSaving(true); setConfigMsg('');
    try {
      await apiPut(`/api/config/${selectedConfig}`, { content: configContent });
      setConfigMsg(`✓ ${selectedConfig} 저장 완료`);
    } catch (e) {
      setConfigMsg(`✗ ${e.message}`);
    } finally {
      setConfigSaving(false);
    }
  }

  useEffect(() => { loadConfigList(); }, []);

  const MODELS = [
    'claude-opus-4-6',
    'claude-sonnet-4-6',
    'claude-haiku-4-5-20251001',
  ];

  useEffect(() => {
    loadRefs();
    loadAiSettings();
  }, []);

  async function loadRefs() {
    try {
      const data = await apiGet('/api/references');
      setRefs(data.references || []);
    } catch (e) {}
  }

  async function loadAiSettings() {
    try {
      const data = await apiGet('/api/ai-settings');
      setAiKeySet(data.api_key_set);
      setAiPreview(data.api_key_preview || '');
      setAiModel(data.model || 'claude-opus-4-6');
    } catch (e) {}
  }

  async function saveAiSettings() {
    setSaving(true); setSaveMsg('');
    try {
      await apiPut('/api/ai-settings', {
        api_key: aiKey || undefined,
        model: aiModel,
      });
      setSaveMsg('✓ AI 설정 저장 완료');
      setAiKey('');
      await loadAiSettings();
    } catch (e) {
      setSaveMsg(`✗ 오류: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  const [promoting, setPromoting] = useState('');
  const [registering, setRegistering] = useState('');

  async function reloadConfig() {
    try { await loadConfigFile('device_models.ini'); } catch (e) {}
  }

  async function registerModel(ref) {
    setRegistering(ref.name);
    try {
      const data = await apiPost('/api/add-model', {
        model_name: ref.manufacturer || ref.name,
        protocol_name: ref.name,
        device_type: 'inverter',
        iv_scan: ref.iv_scan || false,
        kdn: ref.der_avm || false,
      });
      alert(`모델 등록 완료: ${data.model_name} (id=${data.model_id})`);
      await loadRefs();
      await reloadConfig();
    } catch (e) {
      alert(e.message);
    } finally {
      setRegistering('');
    }
  }

  async function promoteRef(name) {
    if (!confirm(`"${name}"을(를) 검증 완료(내장)로 승격하시겠습니까?\ncommon/ 폴더에 _mm_registers.py로 복사됩니다.`)) return;
    setPromoting(name);
    try {
      await apiPost(`/api/references/${name}/promote`);
      await loadRefs();
      await reloadConfig();
    } catch (e) {
      alert(e.message);
    } finally {
      setPromoting('');
    }
  }

  async function deleteRef(name) {
    if (!confirm(`"${name}" 레퍼런스를 삭제하시겠습니까?\n레퍼런스, config, common 파일이 모두 삭제됩니다.`)) return;
    setDeleting(name);
    try {
      await apiDelete(`/api/references/${name}`);
      await loadRefs();
      await reloadConfig();
    } catch (e) {
      alert(e.message);
    } finally {
      setDeleting('');
    }
  }

  return h('div', { className: 'space-y-6' },

    // Reference Protocol Library (builtin)
    h('div', { className: 'bg-gray-800 rounded-lg p-4 space-y-4' },
      h('div', { className: 'flex items-center justify-between' },
        h('h2', { className: 'text-base font-semibold text-gray-200' }, '레퍼런스 프로토콜 라이브러리'),
        h('button', {
          onClick: loadRefs,
          className: 'text-xs text-blue-400 hover:text-blue-300',
        }, '↻ 새로고침')
      ),

      (() => {
        const builtinRefs = refs.filter(r => r.builtin);
        return builtinRefs.length === 0
          ? h('div', { className: 'text-gray-400 text-sm' }, '레퍼런스 없음')
          : h('div', { className: 'overflow-auto rounded border border-gray-700' },
              h('table', { className: 'w-full text-xs' },
                h('thead', { className: 'bg-gray-700' },
                  h('tr', null,
                    ['No', '이름', '제조사', '설명', '용량', 'MPPT', '스트링', 'FC', 'IV', 'DER'].map(col =>
                      h('th', {
                        key: col,
                        className: 'px-3 py-1.5 text-left text-gray-300 whitespace-nowrap',
                      }, col)
                    )
                  )
                ),
                h('tbody', null,
                  builtinRefs.map(ref =>
                    h('tr', {
                      key: ref.name,
                      className: 'border-t border-gray-700 hover:bg-gray-700/50',
                    },
                      h('td', { className: 'px-3 py-1.5 text-center text-gray-300' },
                        ref.protocol_id || '-'),
                      h('td', { className: 'px-3 py-1.5 font-mono text-blue-300' }, ref.name),
                      h('td', { className: 'px-3 py-1.5 text-gray-300' }, ref.manufacturer || '-'),
                      h('td', { className: 'px-3 py-1.5 text-gray-300 max-w-xs truncate' },
                        ref.description || '-'),
                      h('td', { className: 'px-3 py-1.5 text-center text-gray-300' }, ref.capacity || '-'),
                      h('td', { className: 'px-3 py-1.5 text-center' }, ref.mppt_count),
                      h('td', { className: 'px-3 py-1.5 text-center' }, ref.string_count),
                      h('td', { className: 'px-3 py-1.5 text-center' }, ref.fc_code),
                      h('td', { className: 'px-3 py-1.5 text-center' },
                        ref.iv_scan ? h('span', { className: 'text-green-400' }, 'O') : h('span', { className: 'text-gray-500' }, '-')),
                      h('td', { className: 'px-3 py-1.5 text-center' },
                        ref.der_avm ? h('span', { className: 'text-green-400' }, 'O') : h('span', { className: 'text-gray-500' }, '-')),
                    )
                  )
                )
              )
            );
      })()
    ),

    // User Protocol Library
    h('div', { className: 'bg-gray-800 rounded-lg p-4 space-y-4' },
      h('h2', { className: 'text-base font-semibold text-gray-200' }, '사용자 프로토콜 라이브러리'),

      (() => {
        const userRefs = refs.filter(r => !r.builtin);
        return userRefs.length === 0
          ? h('div', { className: 'text-gray-400 text-sm' }, '사용자 프로토콜 없음')
          : h('div', { className: 'overflow-auto rounded border border-gray-700' },
              h('table', { className: 'w-full text-xs' },
                h('thead', { className: 'bg-gray-700' },
                  h('tr', null,
                    ['No', '이름', '제조사', '설명', '용량', 'MPPT', '스트링', 'FC', 'IV', 'DER', ''].map(col =>
                      h('th', {
                        key: col,
                        className: 'px-3 py-1.5 text-left text-gray-300 whitespace-nowrap',
                      }, col)
                    )
                  )
                ),
                h('tbody', null,
                  userRefs.map(ref =>
                    h('tr', {
                      key: ref.name,
                      className: 'border-t border-gray-700 hover:bg-gray-700/50',
                    },
                      h('td', { className: 'px-3 py-1.5 text-center text-gray-300' },
                        ref.protocol_id || '-'),
                      h('td', { className: 'px-3 py-1.5 font-mono text-blue-300' }, ref.name),
                      h('td', { className: 'px-3 py-1.5 text-gray-300' }, ref.manufacturer || '-'),
                      h('td', { className: 'px-3 py-1.5 text-gray-300 max-w-xs truncate' },
                        ref.description || '-'),
                      h('td', { className: 'px-3 py-1.5 text-center text-gray-300' }, ref.capacity || '-'),
                      h('td', { className: 'px-3 py-1.5 text-center' }, ref.mppt_count),
                      h('td', { className: 'px-3 py-1.5 text-center' }, ref.string_count),
                      h('td', { className: 'px-3 py-1.5 text-center' }, ref.fc_code),
                      h('td', { className: 'px-3 py-1.5 text-center' },
                        ref.iv_scan ? h('span', { className: 'text-green-400' }, 'O') : h('span', { className: 'text-gray-500' }, '-')),
                      h('td', { className: 'px-3 py-1.5 text-center' },
                        ref.der_avm ? h('span', { className: 'text-green-400' }, 'O') : h('span', { className: 'text-gray-500' }, '-')),
                      h('td', { className: 'px-3 py-1.5' },
                        h('div', { className: 'flex gap-1' },
                          !ref.protocol_id && h(Button, {
                            onClick: () => registerModel(ref),
                            disabled: registering === ref.name,
                            variant: 'primary',
                            size: 'sm',
                          },
                            registering === ref.name ? h(Spinner) : '등록'
                          ),
                          h(Button, {
                            onClick: () => deleteRef(ref.name),
                            disabled: deleting === ref.name,
                            variant: 'danger',
                            size: 'sm',
                          },
                            deleting === ref.name ? h(Spinner) : '삭제'
                          ),
                          ref.protocol_id && h(Button, {
                            onClick: () => promoteRef(ref.name),
                            disabled: promoting === ref.name,
                            variant: 'success',
                            size: 'sm',
                          },
                            promoting === ref.name ? h(Spinner) : '검증완료'
                          ),
                        )
                      )
                    )
                  )
                )
              )
            );
      })()
    ),

    // Config File Management
    h('div', { className: 'bg-gray-800 rounded-lg p-4 space-y-4' },
      h('h2', { className: 'text-base font-semibold text-gray-200' }, 'Config 파일 관리'),

      h('div', { className: 'flex items-center gap-2 flex-wrap' },
        configFiles.map(f =>
          h('button', {
            key: f.name,
            onClick: () => loadConfigFile(f.name),
            className: cls('px-3 py-1.5 text-xs rounded transition-colors',
              selectedConfig === f.name
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'),
          }, f.name)
        )
      ),

      selectedConfig && h('div', { className: 'space-y-2' },
        h('div', { className: 'flex items-center justify-between' },
          h('span', { className: 'text-sm text-gray-300 font-mono' }, selectedConfig),
          h('div', { className: 'flex items-center gap-2' },
            h(Button, {
              onClick: saveConfigFile,
              disabled: configSaving,
              variant: 'success',
              size: 'sm',
            },
              configSaving ? h(Spinner) : '💾',
              configSaving ? '저장 중...' : '저장'
            ),
            configMsg && h('span', {
              className: cls('text-xs', configMsg.startsWith('✓') ? 'text-green-400' : 'text-red-400'),
            }, configMsg)
          )
        ),
        h('textarea', {
              value: configContent,
              onChange: e => setConfigContent(e.target.value),
              className: 'w-full h-80 bg-gray-900 border border-gray-700 rounded p-3 text-xs text-green-300 font-mono resize-y',
              spellCheck: false,
            })
      ),

      !selectedConfig && h('div', { className: 'text-gray-400 text-sm' }, '파일을 선택하세요')
    )
  );
}

// ---------------------------------------------------------------------------
// App Root
// ---------------------------------------------------------------------------
function App() {
  const [tab, setTab] = useState(0);
  const [showAbout, setShowAbout] = useState(false);
  const sessionId = useMemo(() => getSessionId(), []);
  const [pdfInfo, setPdfInfo] = useState(null);
  const [stage1Done, setStage1Done] = useState(false);
  const [stage2Done, setStage2Done] = useState(false);
  const [detected, setDetected] = useState({ mppt_count: 4, string_count: 8, iv_scan: false });
  const [wsConnected, setWsConnected] = useState(false);
  const [wsMessages, setWsMessages] = useState([]);
  const wsRef = useRef(null);
  const reconnectRef = useRef(null);

  // WebSocket setup
  function connectWS() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/ws`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsConnected(true);
      if (reconnectRef.current) { clearTimeout(reconnectRef.current); reconnectRef.current = null; }
    };

    ws.onclose = () => {
      setWsConnected(false);
      reconnectRef.current = setTimeout(connectWS, 3000);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = e => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.session_id && msg.session_id !== sessionId) return;
        setWsMessages(prev => [...prev.slice(-200), msg]);

        // Track stage completion
        if (msg.type === 'done' && msg.success) {
          if (msg.stage === 'stage1') setStage1Done(true);
          if (msg.stage === 'stage2') setStage2Done(true);
        }
      } catch {}
    };
  }

  useEffect(() => {
    connectWS();
    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
  }, []);

  // Keep only messages for the active stage context
  const tabMessages = useMemo(() => {
    const stageMap = ['stage1', 'stage2', 'stage3', null];
    const stage = stageMap[tab];
    if (!stage) return [];
    return wsMessages.filter(m => m.stage === stage);
  }, [wsMessages, tab]);

  const TABS = [
    { label: 'Stage 1 — 추출', icon: '📄' },
    { label: 'Stage 2 — 자동 매핑', icon: '🔗' },
    { label: 'Stage 3 — 코드 생성', icon: '⚙️' },
    { label: '관리', icon: '🔧' },
  ];

  return h('div', { className: 'min-h-screen bg-gray-900 text-gray-100' },

    // Header
    h(AboutModal, { open: showAbout, onClose: () => setShowAbout(false) }),

    h('header', { className: 'bg-gray-800 border-b border-gray-700 px-4 py-3 flex items-center justify-between' },
      h('div', { className: 'flex items-center gap-3' },
        h('button', {
          onClick: () => setShowAbout(true),
          className: 'text-gray-300 hover:text-white hover:bg-gray-700 rounded-md px-2 py-1 transition-colors text-sm',
          title: 'About',
        }, 'ℹ️'),
        h('div', { className: 'text-lg font-bold text-blue-300' }, 'Model Maker Web'),
        h('span', { className: 'text-xs text-gray-300' }, 'v1.0.0'),
        h('span', { className: 'text-xs text-gray-200 hidden sm:block' },
          '— RTU UDP System 인버터 레지스터맵 생성기')
      ),
      h('div', { className: 'flex items-center gap-3' },
        pdfInfo && h('span', { className: 'text-xs text-gray-300 hidden sm:block truncate max-w-xs' },
          `📄 ${pdfInfo.filename}`
        ),
        h('div', { className: 'flex items-center gap-1.5' },
          h('div', {
            className: cls('w-2 h-2 rounded-full', wsConnected ? 'bg-green-500' : 'bg-red-500'),
          }),
          h('span', { className: 'text-xs text-gray-400' },
            wsConnected ? 'WS 연결됨' : 'WS 재연결 중...')
        )
      )
    ),

    // Pipeline Status Bar
    h('div', { className: 'bg-gray-800 border-b border-gray-700 px-4 py-2 flex items-center gap-4 text-xs' },
      h('span', { className: 'text-gray-400' }, '진행 상태:'),
      [
        { label: 'PDF 업로드', done: !!pdfInfo },
        { label: 'Stage 1', done: stage1Done },
        { label: 'Stage 2', done: stage2Done },
      ].map(({ label, done }, i) =>
        h('div', { key: i, className: 'flex items-center gap-1' },
          i > 0 && h('span', { className: 'text-gray-500' }, '→'),
          h('span', {
            className: cls(done ? 'text-green-400' : 'text-gray-400'),
          }, done ? `✓ ${label}` : label)
        )
      )
    ),

    // Tabs
    h('div', { className: 'bg-gray-800 border-b border-gray-700 px-4' },
      h('div', { className: 'flex gap-0' },
        TABS.map((t, i) =>
          h('button', {
            key: i,
            onClick: () => setTab(i),
            className: cls(
              'px-4 py-3 text-sm font-medium transition-colors border-b-2 whitespace-nowrap',
              tab === i
                ? 'border-blue-500 text-blue-400 bg-gray-900/50'
                : 'border-transparent text-gray-300 hover:text-gray-200 hover:border-gray-500'
            ),
          },
            h('span', { className: 'mr-1.5' }, t.icon),
            t.label
          )
        )
      )
    ),

    // Tab Content
    h('main', { className: 'max-w-5xl mx-auto px-4 py-6' },
      tab === 0 && h(Stage1Tab, {
        sessionId,
        pdfInfo,
        onPdfUploaded: info => {
          setPdfInfo(info);
          setStage1Done(false);
          setStage2Done(false);
        },
        onDetected: d => setDetected(d),
        wsMessages: tabMessages,
      }),
      tab === 1 && h(Stage2Tab, {
        sessionId,
        stage1Done,
        detected,
        wsMessages: tabMessages,
      }),
      tab === 2 && h(Stage3Tab, {
        sessionId,
        stage2Done,
        detected,
        wsMessages: tabMessages,
      }),
      tab === 3 && h(ManagementTab, { sessionId }),
    ),

    // Footer
    h('footer', { className: 'border-t border-gray-800 px-4 py-3 text-center text-xs text-gray-500' },
      'RTU UDP System V1.1.0 — Model Maker Web v1.0.0'
    )
  );
}

// ---------------------------------------------------------------------------
// Mount
// ---------------------------------------------------------------------------
const rootEl = document.getElementById('root');
ReactDOM.createRoot(rootEl).render(h(App));
