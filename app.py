import streamlit as st
import pandas as pd
from jinja2 import Environment
import os
import base64
import json
import re
import time
import io
import threading
import urllib.parse
import urllib.request
from datetime import datetime
import google.generativeai as genai
from PIL import Image
from streamlit.runtime.scriptrunner import add_script_run_ctx
import pymupdf  # fitz 대체 최신 API

# 구글 시트 연동 라이브러리 예외 처리
try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

# ==========================================
# 0. 보안 비밀번호 및 환경 설정
# ==========================================
def get_secret(key, default=""):
    try:
        if key in st.secrets: return st.secrets[key]
    except Exception: pass
    return default

ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "admin0915")
SAVE_PASSWORD = get_secret("SAVE_PASSWORD", "0915")
DEFAULT_GEMINI_KEY = get_secret("GEMINI_API_KEY", "")

FLAG_OPTIONS = [
    "Panama", "Liberia", "Marshall Islands", "Hong Kong", "Singapore", 
    "Korea (KR)", "Bahamas", "Malta", "Cyprus", "India", "China", "Greece", "UK"
]

CLASS_OPTIONS = [
    "ABS", "BV", "CCS", "CRS", "DNV", "IRS", "KR", "LR", 
    "NK", "PRS", "RINA", "TL", "Non-IACS", "KR & NK", "DNV & LR", "IRS & DNV", "Panama / KR"
]

CURRENCY_OPTIONS = [
    "KRW", "USD", "EUR", "JPY", "CNY", "SGD", "GBP", "HKD", "AED"
]

STATUS_OPTIONS = [
    "🟡 Quoted", "🔵 PO Received", "🟣 Invoiced", "🟢 Paid", "🔴 Cancelled", "⚪ Draft"
]

GOOGLE_CLIENT_ID = get_secret("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = get_secret("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = get_secret("REDIRECT_URI")
ALLOWED_DOMAIN = get_secret("ALLOWED_DOMAIN", "1solution.co.kr")

# 서류 대장 컬럼 (헤더 정보 중심)
doc_db_cols = [
    "IssueDate", "DocDate", "DocType", "OurRef", "YourRef", 
    "ShipName", "TargetName", "Currency", "TotalAmount", "ItemCount", "CreatedBy", "Status"
]

# 자재 단가 마스터 컬럼
item_master_cols = [
    "PartNo", "ItemName", "Description", "Supplier", "BuyPrice", "ListPrice", "Currency", "Remarks"
]

OUR_DB_FILE = "our_db"
CUSTOMER_DB_FILE = "customer_db"
ITEM_MASTER_FILE = "item_master"

# ==========================================
# 0-1. 최상단 공통 헬퍼 함수 & 구글 시트 연동
# ==========================================
def clean_str(val):
    if pd.isna(val) or val is None: return ""
    s = str(val).strip()
    return "" if s.lower() in ['nan', 'none', 'null', '<na>', 'nan.0', 'none.0'] else s

def clean_df(df):
    if df is None or df.empty: return df
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].apply(clean_str)
    return df

def ensure_cols(df, target_cols):
    if df is None or df.empty:
        return pd.DataFrame(columns=target_cols)
    df = df.copy()
    for col in target_cols:
        if col not in df.columns:
            df[col] = "-"
    return df[target_cols]

def get_exchange_rates():
    return {
        "USD": 1.0, "KRW": 1350.0, "EUR": 0.92, "JPY": 150.0,
        "CNY": 7.2, "SGD": 1.35, "GBP": 0.79, "HKD": 7.8, "AED": 3.67
    }

def safe_float(val, default=0.0):
    if val is None or pd.isna(val): return default
    s = str(val).replace(',', '').strip()
    match = re.search(r"[-+]?\d*\.\d+|\d+", s)
    if match:
        try: return float(match.group())
        except ValueError: return default
    return default

def get_currency_symbol(code):
    c = clean_str(code).upper()
    symbols = {
        "USD": "$", "KRW": "₩", "EUR": "€", "JPY": "¥",
        "CNY": "¥", "SGD": "S$", "GBP": "£", "HKD": "HK$", "AED": "AED "
    }
    return symbols.get(c, f"{c} " if c else "")

# 구글 시트 클라이언트 로드
def get_gsheet_client():
    if not HAS_GSPREAD or "gcp_service_account" not in st.secrets:
        return None
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(creds)
    except Exception:
        return None

def safe_read_db(db_name, default_cols=None):
    if default_cols is None: default_cols = []
    
    # 1. 구글 시트 읽기 시도
    gc = get_gsheet_client()
    spreadsheet_key = get_secret("SPREADSHEET_KEY")
    if gc and spreadsheet_key:
        try:
            sh = gc.open_by_key(spreadsheet_key)
            ws = sh.worksheet(db_name)
            data = ws.get_all_records()
            if data:
                df = pd.DataFrame(data)
                return ensure_cols(df, default_cols)
        except Exception: pass

    # 2. 구글 시트 실패 시 로컬 CSV 읽기
    filepath = f"{db_name}.csv"
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return pd.DataFrame(columns=default_cols)
    try:
        df = pd.read_csv(filepath)
        return ensure_cols(df, default_cols)
    except Exception:
        return pd.DataFrame(columns=default_cols)

def safe_save_db(df, db_name, default_cols):
    clean_target = ensure_cols(clean_df(df), default_cols)
    
    # 1. 로컬 CSV 보존
    clean_target.to_csv(f"{db_name}.csv", index=False)
    
    # 2. 구글 시트 동기화
    gc = get_gsheet_client()
    spreadsheet_key = get_secret("SPREADSHEET_KEY")
    if gc and spreadsheet_key:
        try:
            sh = gc.open_by_key(spreadsheet_key)
            try:
                ws = sh.worksheet(db_name)
            except Exception:
                ws = sh.add_worksheet(title=db_name, rows="1000", cols="20")
            ws.clear()
            ws.update([clean_target.columns.values.tolist()] + clean_target.fillna("").values.tolist())
        except Exception as e:
            st.warning(f"⚠️ 구글 시트 동기화 실패 (로컬 CSV에 저장됨): {e}")

def render_unified_input(label, current_val, base_options, key_prefix):
    display_label = f"▾ {label}" if not label.startswith("▾") else label
    curr = clean_str(current_val)
    direct_label = "✏️ 직접 입력 / Direct Input"
    
    options = [""]
    if curr and curr not in options and direct_label not in curr and "직접 입력" not in curr:
        options.append(curr)
        
    for item in base_options:
        s_item = clean_str(item)
        if s_item and s_item not in options and direct_label not in s_item and "직접 입력" not in s_item and "Choose an option" not in s_item:
            options.append(s_item)
            
    options.append(direct_label)
    
    sel_key = f"{key_prefix}_sel"
    txt_key = f"{key_prefix}_txt"
    
    if sel_key not in st.session_state:
        st.session_state[sel_key] = curr if curr in options else ""
    elif st.session_state[sel_key] not in options:
        options.insert(1, st.session_state[sel_key])

    selected = st.selectbox(display_label, options=options, key=sel_key)
    
    if selected == direct_label:
        val = st.text_input(f"{label} ({'직접 입력' if st.session_state.get('lang') == 'KR' else 'Direct Input'})", key=txt_key)
        return val
    else:
        return selected

# ==========================================
# 0-2. i18n 다국어 사전
# ==========================================
TRANSLATIONS = {
    "KR": {
        "subtitle": "사내 임직원 전용 서류 및 자재 관리 시스템",
        "google_login": "🔑 Google 계정으로 로그인",
        "test_login": "🚀 테스트 로그인",
        "logout": "🚪 로그아웃",
        "user_label": "👤 접속자:",
        "sys_menu": "SYSTEM MENU",
        "menu_gen": "서류 분석 / 생성 Master",
        "menu_doc_ledger": "서류 관리 대장 (자사 / 고객사)",
        "menu_item_master": "자재 단가 마스터 DB",
        "menu_history": "서류 이력",
        "menu_admin": "🛠️ 관리자 메뉴",
        "doc_gen_title": "📄 서류 분석 및 자동 생성 Master System",
        "doc_gen_desc": "AI 문서 분석을 기반으로 고정 양식 및 DB 연동 생성을 지원하며, 모든 항목은 직접 수정 가능합니다.",
        "ai_expander_title": "⚡ AI 문서 자동 분석 (클릭하여 열기) 🔽",
        "ai_mode_label": "AI 분석 엔진 선택",
        "mode_flash": "⚡ Gemini 3.6 Flash (고속)",
        "mode_thinking": "🧠 Gemini 3.6 Flash (사고)",
        "upload_doc_label": "문서 및 파일 업로드 (PDF, JPG, PNG, XLSX, CSV)",
        "btn_ai_parse": "✨ AI 문서 분석",
        "btn_reset": "🔄 입력 초기화",
        "hdr_title": "📌 {doc_type} 헤더 입력 (모든 항목 직접 입력 가능)",
        "items_title": "📦 품목 상세 내역 (줄바꿈/엔터 지원 / 열 너비 자동 맞춤)",
        "remarks_title": "📝 Remarks & Deviations",
        "reg_title": "📌 DB 데이터 등록 및 저장",
        "pwd_save_label": "🔒 비밀번호",
        "btn_register": "📥 자사 서류 대장에 헤더 등록",
        "preview_title": "⚡ 실시간 PDF 문서 미리보기",
        "btn_download_pdf": "💾 완성된 PDF 다운로드",
        "doc_ledger_title": "📊 서류 통합 관리 대장",
        "item_master_title": "📦 자재 단가 마스터 DB 관리",
        "filter_category": "1️⃣ 필터 항목 선택",
        "filter_value": "2️⃣ 하위 값 선택",
        "filter_keyword": "🔎 키워드 통합 검색",
        "filter_keyword_ph": "검색어 입력...",
        "total_records": "**총 `{count}` 건 조회됨** (전체 `{total}` 건 중)",
        "btn_download_csv": "📥 필터링된 결과 엑셀(CSV) 다운로드",
        "no_ledger": "등록된 내역이 없습니다.",
        "ai_db_title": "🤖 AI DB 수집기",
        "upload_db_label": "DB 파일/문서 업로드 (PDF, JPG, PNG, XLSX, CSV)",
        "parse_mode": "파싱 모드",
        "parse_mode_sheet": "📌 특정 시트 선택",
        "parse_mode_all": "🚀 전체 시트 파싱",
        "select_sheet": "시트 선택",
        "btn_analyze": "✨ 분석",
        "btn_parse_all": "🚀 전체 파싱",
        "btn_final_db_save": "✅ DB 최종 저장",
        "btn_save_db": "💾 DB 수정사항 저장",
        "pwd_admin_label": "관리자 비밀번호 입력",
        "pwd_err": "❌ 비밀번호가 올바르지 않습니다.",
        "reg_success": "🎉 DB 등록 완료",
        "all": "전체",
    },
    "EN": {
        "subtitle": "In-house Document & Material Management System",
        "google_login": "🔑 Sign in with Google",
        "test_login": "🚀 Test Login",
        "logout": "🚪 Logout",
        "user_label": "👤 User:",
        "sys_menu": "SYSTEM MENU",
        "menu_gen": "Doc Analysis / Gen Master",
        "menu_doc_ledger": "Document Ledger (Our / Customer)",
        "menu_item_master": "Item Price Master DB",
        "menu_history": "Document History",
        "menu_admin": "🛠️ Admin Menu",
        "doc_gen_title": "📄 Document Analysis & Generation Master System",
        "doc_gen_desc": "Supports fixed template & DB linked generation. All fields are 100% human-editable.",
        "ai_expander_title": "⚡ AI Document Auto-Analysis (Click to Expand) 🔽",
        "ai_mode_label": "Select AI Engine",
        "mode_flash": "⚡ Gemini 3.6 Flash (Fast)",
        "mode_thinking": "🧠 Gemini 3.6 Flash (Thinking)",
        "upload_doc_label": "Upload Document/Data (PDF, JPG, PNG, XLSX, CSV)",
        "btn_ai_parse": "✨ Analyze Document",
        "btn_reset": "🔄 Reset",
        "hdr_title": "📌 {doc_type} Header Details (Direct input supported)",
        "items_title": "📦 Line Item Details (Multi-line supported / Auto-fit)",
        "remarks_title": "📝 Remarks & Deviations",
        "reg_title": "📌 Save Data to DB",
        "pwd_save_label": "🔒 Password",
        "btn_register": "📥 Save Header to Document Ledger",
        "preview_title": "⚡ Live PDF Document Preview",
        "btn_download_pdf": "💾 Download PDF Document",
        "doc_ledger_title": "📊 Document Ledger Management",
        "item_master_title": "📦 Item Price Master DB",
        "filter_category": "1️⃣ Select Filter Column",
        "filter_value": "2️⃣ Select Sub-value",
        "filter_keyword": "🔎 Search Keyword",
        "filter_keyword_ph": "Type keyword...",
        "total_records": "**Total `{count}` record(s) found** (Out of `{total}`)",
        "btn_download_csv": "📥 Download Filtered Excel (CSV)",
        "no_ledger": "No records found.",
        "ai_db_title": "🤖 AI DB Collector",
        "upload_db_label": "Upload DB File/Document (PDF, JPG, PNG, XLSX, CSV)",
        "parse_mode": "Parsing Mode",
        "parse_mode_sheet": "📌 Select Specific Sheet",
        "parse_mode_all": "🚀 Parse All Sheets",
        "select_sheet": "Select Sheet",
        "btn_analyze": "✨ Analyze",
        "btn_parse_all": "🚀 Parse All",
        "btn_final_db_save": "✅ Save to DB",
        "btn_save_db": "💾 Save DB Changes",
        "pwd_admin_label": "Enter Admin Password",
        "pwd_err": "❌ Incorrect password.",
        "reg_success": "🎉 Saved to DB",
        "all": "All",
    }
}

def t(key, **kwargs):
    lang = st.session_state.get('lang', 'KR')
    text = TRANSLATIONS.get(lang, TRANSLATIONS['KR']).get(key, key)
    return text.format(**kwargs) if kwargs else text

# ==========================================
# 0-3. 구글 OAuth 로그인 기능
# ==========================================
def get_google_auth_url():
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile",
        "access_type": "offline",
        "prompt": "select_account"
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

def get_google_user_info(code):
    token_url = "https://oauth2.googleapis.com/token"
    payload = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }).encode('utf-8')
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    req = urllib.request.Request(token_url, data=payload, headers=headers)
    with urllib.request.urlopen(req) as response:
        token_data = json.loads(response.read().decode('utf-8'))
        
    access_token = token_data.get("access_token")
    userinfo_url = f"https://www.googleapis.com/oauth2/v2/userinfo?access_token={access_token}"
    req_user = urllib.request.Request(userinfo_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req_user) as response_user:
        return json.loads(response_user.read().decode('utf-8'))

# ==========================================
# 1. 페이지 설정 & CSS
# ==========================================
st.set_page_config(page_title="ONE - ERP", layout="wide", page_icon="🚢")

if 'lang' not in st.session_state: st.session_state['lang'] = 'KR'

custom_css = """
<style>
    .main .block-container { padding-top: 1.2rem !important; padding-bottom: 1rem !important; }
    
    div[data-testid="stRadio"]:has(input[aria-label="Language"]),
    div[data-testid="stRadio"]:has(input[value="🇰🇷"]) {
        position: fixed !important; top: 10px !important; right: 175px !important;
        z-index: 999999 !important; background: rgba(15, 23, 42, 0.9) !important;
        border: 1px solid #0284C7 !important; padding: 2px 10px !important;
        border-radius: 18px !important; box-shadow: 0 2px 8px rgba(0,0,0,0.3) !important;
    }
    div[data-testid="stRadio"]:has(input[aria-label="Language"]) > div,
    div[data-testid="stRadio"]:has(input[value="🇰🇷"]) > div { flex-direction: row !important; gap: 10px !important; }

    .main-header { background: var(--secondary-background-color); border: 2px solid #0284C7; border-left: 6px solid #0284C7; padding: 16px 20px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
    .main-header h1 { color: var(--text-color); font-size: 1.5rem; font-weight: 800; margin: 0; }
    .main-header p { color: var(--text-color); opacity: 0.85; margin: 4px 0 0 0; font-size: 0.85rem; font-weight: 500; }
    .section-title { color: #0284C7; font-size: 1.05rem; font-weight: 800; margin-bottom: 12px; }

    .google-btn { 
        display: inline-flex !important; align-items: center !important; justify-content: center !important;
        width: 100% !important; background: linear-gradient(135deg, #1D4ED8 0%, #0284C7 100%) !important; 
        color: #FFFFFF !important; font-weight: 700 !important; border: none !important; 
        padding: 8px 16px !important; border-radius: 8px !important; font-size: 0.95rem !important; 
        text-decoration: none !important; height: 42px !important; margin-bottom: 12px !important;
    }
    .rate-card { background: rgba(15, 23, 42, 0.6); border: 1px solid #1E293B; border-radius: 8px; padding: 8px 10px; margin-bottom: 12px; font-size: 0.8rem; color: #94A3B8; }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

selected_lang_flag = st.radio("Language", ["🇰🇷", "🇺🇸"], index=0 if st.session_state['lang'] == 'KR' else 1, horizontal=True, label_visibility="collapsed", key="top_lang_radio")
target_lang_code = "KR" if selected_lang_flag == "🇰🇷" else "EN"

if target_lang_code != st.session_state['lang']:
    st.session_state['lang'] = target_lang_code
    st.rerun()

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
    st.session_state['user_email'] = ""

# OAuth 파라미터 체크 및 처리
try:
    code_param = st.query_params.get("code", None)
except Exception:
    code_param = None

if code_param and not st.session_state['authenticated']:
    if st.session_state.get('processed_code') != code_param:
        st.session_state['processed_code'] = code_param
        try:
            user_info = get_google_user_info(code_param)
            email = user_info.get("email", "")
            if ALLOWED_DOMAIN and not email.endswith(f"@{ALLOWED_DOMAIN}"):
                st.error(f"❌ Access Denied: Only @{ALLOWED_DOMAIN} accounts are allowed. ({email})")
                st.query_params.clear()
            else:
                st.session_state['authenticated'] = True
                st.session_state['user_email'] = email
                st.query_params.clear()
                st.rerun()
        except Exception as e:
            st.query_params.clear()
            st.error(f"Google Auth Error: {e}")

if not st.session_state['authenticated']:
    st.write("")
    st.write("")
    _, center_col, _ = st.columns([1, 1.5, 1])
    with center_col:
        with st.container(border=True):
            st.markdown("<h2 style='text-align: center;'>🚢 ONE - ERP</h2>", unsafe_allow_html=True)
            st.markdown(f"<p style='text-align: center; color: #94A3B8;'>{t('subtitle')}</p>", unsafe_allow_html=True)
            
            if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
                auth_url = get_google_auth_url()
                st.markdown(f'<a href="{auth_url}" target="_self" class="google-btn">{t("google_login")}</a>', unsafe_allow_html=True)

            if st.button(t("test_login")):
                st.session_state['authenticated'] = True
                st.session_state['user_email'] = f"sjsohn@{ALLOWED_DOMAIN}"
                st.rerun()
    st.stop()

# ==========================================
# 2. 내장형 PDF HTML 템플릿
# ==========================================
INLINE_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&display=swap');
    @page { size: A4; margin-top: 25mm; margin-bottom: 12mm; margin-left: 8mm; margin-right: 8mm; }
    body { font-family: 'Malgun Gothic', '맑은 고딕', sans-serif; font-size: 8.5pt; line-height: 1.2; color: #000; }
    div.header-repeat { position: fixed; top: -18mm; left: 0; right: 0; border-bottom: 2.5px solid #000; padding-bottom: 2px; }
    .header-table { width: 100%; border-collapse: collapse; }
    .doc-title-text { font-size: 22pt; font-weight: 800; text-align: right; letter-spacing: 1.5px; text-transform: uppercase; color: #0F172A; text-decoration: underline; }
    table.hdr-table { width: 100%; border-collapse: collapse; margin-bottom: 3px; }
    table.hdr-table th, table.hdr-table td { border: 0.9px solid #000; padding: 3px 5px; vertical-align: middle; }
    table.data-table { width: 100%; border-collapse: collapse; margin-bottom: 3px; }
    table.data-table th, table.data-table td { border: 0.9px solid #000; padding: 3px 5px; vertical-align: middle; }
    .hdr-label { width: 16%; font-weight: bold; background-color: #f4f4f4; }
    .hdr-value { width: 34%; }
    .currency { text-align: right; font-weight: bold; font-style: italic; margin-bottom: 2px; }
    .item-th { font-weight: bold; text-align: center; background-color: #f4f4f4; }
    .col-no { width: 5%; text-align: center; }
    .col-desc { width: 55%; white-space: pre-line; word-break: break-word; }
    .col-qty { width: 8%; text-align: center; }
    .col-price { width: 16%; text-align: right; }
    .col-amt { width: 16%; text-align: right; }
    .remarks-box { border: 0.9px solid #000; padding: 3px 5px; margin-top: 2px; font-style: italic; }
    .total-row-td { border: 0.9px solid #000; font-weight: bold; font-size: 10pt; padding: 4px 6px; }
</style>
</head>
<body>
    <div class="header-repeat">
        <table class="header-table">
            <tr>
                <td style="text-align: left; width: 50%;">
                    {% if logo_base64 %}
                    <img src="data:image/png;base64,{{ logo_base64 }}" style="max-height: 58px;" />
                    {% else %}
                    <span style="font-size: 18pt; font-weight: 800; color: #0284C7;">ONE SOLUTION CO., LTD.</span>
                    {% endif %}
                </td>
                <td style="text-align: right; width: 50%;">
                    <div class="doc-title-text">{{ doc_title }}</div>
                </td>
            </tr>
        </table>
    </div>

    <table class="hdr-table">
        <tr>
            <td class="hdr-label">To</td><td class="hdr-value">{{ to_name }}</td>
            <td class="hdr-label">PIC</td><td class="hdr-value">{{ pic }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Attention</td><td class="hdr-value">{{ attn_name }}</td>
            <td class="hdr-label">Date</td><td class="hdr-value">{{ date_str }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Your Ref. No.</td><td class="hdr-value">{{ your_ref }}</td>
            <td class="hdr-label">Our Ref. No.</td><td class="hdr-value">{{ our_ref }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Ship's Name</td><td class="hdr-value">{{ ship_name }}</td>
            <td class="hdr-label">Validity</td><td class="hdr-value">{{ validity }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Flag / Class</td><td class="hdr-value">{{ flag_class }}</td>
            <td class="hdr-label">Payment Due</td><td class="hdr-value">{{ payment_due }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Project Title</td><td class="hdr-value" colspan="3">{{ project_title }}</td>
        </tr>
    </table>

    <div class="currency">Currency: {{ currency }}</div>
    
    <table class="data-table">
        <thead>
            <tr>
                <td class="item-th col-no">No.</td>
                <td class="item-th col-desc">Description (Model, Type, Serial No.)</td>
                <td class="item-th col-qty">Q'ty</td>
                <td class="item-th col-price">Unit Price</td>
                <td class="item-th col-amt">Amount</td>
            </tr>
        </thead>
        <tbody>
            {% for item in items %}
            <tr>
                <td class="col-no">{{ loop.index }}</td>
                <td class="col-desc">{% if item.ItemName %}<strong>{{ item.ItemName | replace('\n', '<br>') }}</strong><br>{% endif %}{% if item.Description and item.Description != item.ItemName %}{{ item.Description | replace('\n', '<br>') }}<br>{% endif %}{% if item.Remarks %}<span style="font-size: 8pt; color: #444;"><em>{{ item.Remarks | replace('\n', '<br>') }}</em></span>{% endif %}</td>
                <td class="col-qty">{{ item.Qty }}</td>
                <td class="col-price">{{ item.UnitPriceFormatted }}</td>
                <td class="col-amt">{{ item.AmountFormatted }}</td>
            </tr>
            {% endfor %}
            {% if bottom_remarks %}
            <tr>
                <td colspan="5" style="border: 0.9px solid #000; padding: 4px 6px; background-color: #fafafa;">
                    <strong><em>[Remarks & Deviations]</em></strong><br>{{ bottom_remarks | replace('\n', '<br>') }}
                </td>
            </tr>
            {% endif %}
            {% if total_amount_str %}
            <tr>
                <td colspan="3" class="total-row-td"></td>
                <td class="total-row-td" style="text-align: center; background-color: #f4f4f4;">Total Amount</td>
                <td class="total-row-td" style="text-align: right; font-size: 11pt;">{{ total_amount_str }}</td>
            </tr>
            {% endif %}
        </tbody>
    </table>

    {% if vat_note %}
    <div style="text-align: right; font-size: 8pt; font-weight: bold;">{{ vat_note }}</div>
    {% endif %}
</body>
</html>
"""

# ==========================================
# 3. 환경 및 데이터 정제 필수 도구
# ==========================================
KEY_FILE = "gemini_key.txt"
HISTORY_FILE = "master_history.json"
INPUT_DOCS_DIR = "input_docs"
os.makedirs("output", exist_ok=True)
os.makedirs(INPUT_DOCS_DIR, exist_ok=True)

def prepare_items_for_pdf(items_list, currency="KRW"):
    sym = get_currency_symbol(currency)
    formatted_items = []
    
    for item in items_list:
        item_copy = dict(item)
        iname = clean_str(item_copy.get('ItemName', ''))
        desc = clean_str(item_copy.get('Description', ''))
        rem = clean_str(item_copy.get('Remarks', ''))
        
        if not any([iname, desc, rem, safe_float(item_copy.get('UnitPrice')) > 0]): continue

        item_copy['ItemName'] = iname
        item_copy['Description'] = desc
        item_copy['Remarks'] = rem
        
        qty_raw = item_copy.get('Qty', '')
        q_val = safe_float(qty_raw, default=None)
        item_copy['Qty'] = f"{int(q_val)}" if q_val and q_val == int(q_val) else (str(qty_raw) if qty_raw else '')

        u_p_val = safe_float(item_copy.get('UnitPrice', 0))
        amt_val = safe_float(item_copy.get('Amount', 0))
            
        fmt_up = f"{u_p_val:,.0f}" if currency in ["KRW", "JPY"] else f"{u_p_val:,.2f}"
        fmt_amt = f"{amt_val:,.0f}" if currency in ["KRW", "JPY"] else f"{amt_val:,.2f}"

        item_copy['UnitPriceFormatted'] = f"{sym}{fmt_up}" if u_p_val > 0 else ""
        item_copy['AmountFormatted'] = f"{sym}{fmt_amt}" if amt_val > 0 else ""
        formatted_items.append(item_copy)
    return formatted_items

if 'bg_task' not in st.session_state:
    st.session_state['bg_task'] = {'status': 'idle', 'type': None, 'progress_msg': '', 'result': None, 'error_msg': None}

is_running = (st.session_state['bg_task']['status'] == 'running')

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {"ships": [], "to_list": [], "attns": []}

def save_history(ship, to, attn):
    data = load_history()
    updated = False
    for key, val in [("ships", ship), ("to_list", to), ("attns", attn)]:
        if val and val.strip() and val not in data[key]:
            data[key].append(val.strip())
            updated = True
    if updated:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def save_to_doc_ledger(target_db_name, doc_type, your_ref, our_ref, ship_name, target_name, doc_date_str, currency, total_amount, item_count, user_email=""):
    df = safe_read_db(target_db_name, doc_db_cols)
    issue_date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    logged_user = user_email or st.session_state.get('user_email', 'Unknown')

    if doc_type == "Quotation": default_status = "🟡 Quoted"
    elif doc_type == "Purchase Order": default_status = "🔵 PO Received"
    elif doc_type == "Invoice": default_status = "🟣 Invoiced"
    else: default_status = "🟡 Quoted"

    new_entry = pd.DataFrame([{
        "IssueDate": issue_date_str, "DocDate": doc_date_str or "-", "DocType": doc_type,
        "OurRef": our_ref or "-", "YourRef": your_ref or "-", "ShipName": ship_name or "-",
        "TargetName": target_name or "-", "Currency": currency or "-", "TotalAmount": total_amount,
        "ItemCount": item_count, "CreatedBy": logged_user, "Status": default_status
    }])

    updated_df = pd.concat([df, new_entry], ignore_index=True)
    safe_save_db(updated_df, target_db_name, doc_db_cols)

def save_items_to_master(items_df, supplier_name="자사 서류 생성", currency="KRW"):
    if items_df is None or items_df.empty: return 0
    master_df = safe_read_db(ITEM_MASTER_FILE, item_master_cols)
    
    new_rows = []
    for _, row in items_df.iterrows():
        pno = clean_str(row.get('PartNo', ''))
        iname = clean_str(row.get('ItemName', ''))
        desc = clean_str(row.get('Description', ''))
        u_price = safe_float(row.get('UnitPrice', 0))
        rem = clean_str(row.get('Remarks', ''))
        
        if pno or iname or desc:
            new_rows.append({
                "PartNo": pno, "ItemName": iname, "Description": desc, "Supplier": supplier_name,
                "BuyPrice": 0.0, "ListPrice": u_price, "Currency": currency, "Remarks": rem
            })
            
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        combined = pd.concat([master_df, new_df], ignore_index=True)
        safe_save_db(combined, ITEM_MASTER_FILE, item_master_cols)
        return len(new_rows)
    return 0

if 'doc_info' not in st.session_state:
    st.session_state['doc_info'] = {"to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "", "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "", "payment_due": "", "currency": "", "bottom_remarks": ""}

if 'doc_items' not in st.session_state:
    st.session_state['doc_items'] = pd.DataFrame([{"PartNo": "", "ItemName": "", "Description": "", "Qty": "", "UnitPrice": "", "Amount": "", "Remarks": ""}])

# ==========================================
# 4. AI 파싱 엔진
# ==========================================
def get_ai_response(api_key, content_list, mode="flash"):
    if not api_key or not str(api_key).strip(): raise Exception("Gemini API Key가 누락되었습니다.")
    genai.configure(api_key=api_key.strip())
    
    primary_model = "gemini-3.6-flash-thinking" if mode == "thinking" else "gemini-3.6-flash"
    candidate_models = [primary_model] if primary_model == "gemini-3.6-flash" else [primary_model, "gemini-3.6-flash"]

    for model_name in candidate_models:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(content_list)
            if response and response.text:
                res_text = response.text.strip()
                s_idx = res_text.find('[') if '[' in res_text and (res_text.find('[') < res_text.find('{') or '{' not in res_text) else res_text.find('{')
                e_idx = res_text.rfind(']') if ']' in res_text and (res_text.rfind(']') > res_text.rfind('}') or '}' not in res_text) else res_text.rfind('}')
                if s_idx != -1 and e_idx != -1: res_text = res_text[s_idx:e_idx + 1]
                return json.loads(res_text)
        except Exception as e:
            last_err = e

    raise Exception(f"Gemini API 요청 실패: {last_err}")

def run_bg_doc_parse(task_state, api_key, file_bytes, file_name, doc_type, ai_mode):
    try:
        task_state['status'] = 'running'
        task_state['progress_msg'] = 'AI 엔진이 문서를 분석 중입니다...'
        
        file_ext = file_name.split('.')[-1].lower()
        prompt = """
        Extract document details into JSON matching this structure:
        {
            "doc_type": "", "issuer_company": "", "issuer_pic": "", "recipient_company": "", "recipient_attn": "",
            "to_name": "", "attn_name": "", "project_title": "", "validity": "", "flag_class": "",
            "our_ref": "", "date_str": "", "pic": "", "your_ref": "", "ship_name": "", "payment_due": "", "currency": "",
            "items": [{"PartNo": "", "ItemName": "", "Description": "", "Qty": "", "UnitPrice": "", "Amount": "", "Remarks": ""}]
        }
        """
        if file_ext in ['png', 'jpg', 'jpeg']:
            content = Image.open(io.BytesIO(file_bytes))
        elif file_ext == 'pdf':
            content = {"mime_type": "application/pdf", "data": file_bytes}
        else:
            content = file_bytes.decode('utf-8', errors='ignore')

        ai_data = get_ai_response(api_key, [prompt, content], mode=ai_mode)
        task_state['result'] = {'doc_type': doc_type, 'ai_data': ai_data, 'file_name': file_name}
        task_state['status'] = 'completed'
    except Exception as e:
        task_state['status'] = 'error'
        task_state['error_msg'] = str(e)

def start_bg_thread(target_func, args):
    t = threading.Thread(target=target_func, args=args)
    add_script_run_ctx(t)
    t.start()

def generate_pdf(context):
    from weasyprint import HTML
    logo_path = os.path.abspath("logo.png")
    context["logo_base64"] = base64.b64encode(open(logo_path, "rb").read()).decode('utf-8') if os.path.exists(logo_path) else None
    
    env = Environment()
    template = env.from_string(INLINE_HTML_TEMPLATE)
    html_out = template.render(context)
    return HTML(string=html_out).write_pdf()

def render_pdf_images(pdf_bytes):
    images = []
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            images.append(pix.tobytes("png"))
    except Exception: pass
    return images

# ==========================================
# 5. UI 및 사이드바 Navigation
# ==========================================
st.sidebar.title("🚢 ONE - ERP")
if st.session_state.get('user_email'):
    st.sidebar.markdown(f"{t('user_label')} `{st.session_state['user_email']}`")
    if st.sidebar.button(t("logout")):
        st.session_state['authenticated'] = False
        st.session_state['user_email'] = ""
        st.rerun()

st.sidebar.markdown("""<div style="background: rgba(2, 132, 199, 0.1); border: 1px solid #0284C7; border-radius: 8px; padding: 10px 12px; text-align: center; margin-bottom: 12px;"><span style="color: #0284C7; font-size: 0.85rem; font-weight: 800;">Powered by Gemini 3.6</span></div>""", unsafe_allow_html=True)

menu_options = [t("menu_gen"), t("menu_doc_ledger"), t("menu_item_master"), t("menu_history"), t("menu_admin")]
menu_selection = st.sidebar.radio(t("sys_menu"), menu_options)

if menu_selection == t("menu_gen"): menu = "서류 분석 / 생성 Master"
elif menu_selection == t("menu_doc_ledger"): menu = "서류 관리 대장"
elif menu_selection == t("menu_item_master"): menu = "자재 단가 마스터 DB"
elif menu_selection == t("menu_admin"): menu = "관리자 메뉴"
else: menu = "서류 이력"

task = st.session_state['bg_task']

# ==========================================
# 6. 서류 분석 / 생성 Master
# ==========================================
if menu == "서류 분석 / 생성 Master":
    doc_type = st.sidebar.selectbox("📋 " + ("Document Type" if st.session_state['lang'] == "EN" else "서류 유형 선택"), ["Quotation", "Purchase Order", "Invoice", "Delivery Note", "Service Report", "Credit Note"])
    st.markdown(f"""<div class="main-header"><h1>{t('doc_gen_title')} ({doc_type})</h1><p>{t('doc_gen_desc')}</p></div>""", unsafe_allow_html=True)

    our_ledger = safe_read_db(OUR_DB_FILE, doc_db_cols)
    cust_ledger = safe_read_db(CUSTOMER_DB_FILE, doc_db_cols)
    history = load_history()

    db_to_options = sorted(list(set([str(x).strip() for x in (our_ledger["TargetName"].tolist() + cust_ledger["TargetName"].tolist() + history.get("to_list", [])) if str(x).strip() and str(x).strip() not in ["-", "nan", "None"]])))
    db_ship_options = sorted(list(set([str(x).strip() for x in (our_ledger["ShipName"].tolist() + cust_ledger["ShipName"].tolist() + history.get("ships", [])) if str(x).strip() and str(x).strip() not in ["-", "nan", "None"]])))

    if task['status'] == 'completed' and task['type'] == 'doc_parse':
        ai_data = task['result']['ai_data']
        st.session_state['doc_info'] = {
            "to": clean_str(ai_data.get("recipient_company", "") or ai_data.get("to_name", "")),
            "attn": clean_str(ai_data.get("recipient_attn", "") or ai_data.get("attn_name", "")),
            "project_title": clean_str(ai_data.get("project_title", "")),
            "validity": clean_str(ai_data.get("validity", "30 Days")),
            "flag_class": clean_str(ai_data.get("flag_class", "")),
            "our_ref": clean_str(ai_data.get("our_ref", "")),
            "date": clean_str(ai_data.get("date_str", datetime.now().strftime("%Y-%m-%d"))),
            "pic": clean_str(ai_data.get("issuer_pic", "") or ai_data.get("pic", "")),
            "your_ref": clean_str(ai_data.get("your_ref", "")),
            "ship": clean_str(ai_data.get("ship_name", "")),
            "payment_due": clean_str(ai_data.get("payment_due", "")),
            "currency": clean_str(ai_data.get("currency", "KRW")),
            "bottom_remarks": ""
        }
        parsed_items = ai_data.get("items", [])
        if parsed_items:
            st.session_state['doc_items'] = clean_df(pd.DataFrame(parsed_items))
        st.session_state['bg_task']['status'] = 'idle'
        st.success("✅ AI 문서 분석 완료")

    left_col, right_col = st.columns([5, 5])

    with left_col:
        with st.expander(t("ai_expander_title"), expanded=False):
            ai_mode_choice = st.radio(t("ai_mode_label"), [t("mode_flash"), t("mode_thinking")], horizontal=True)
            uploaded_doc = st.file_uploader(t("upload_doc_label"), type=["pdf", "png", "jpg", "jpeg", "xlsx", "csv"])
            if uploaded_doc and st.button(t("btn_ai_parse")):
                st.session_state['bg_task']['type'] = 'doc_parse'
                start_bg_thread(run_bg_doc_parse, (st.session_state['bg_task'], DEFAULT_GEMINI_KEY, uploaded_doc.getvalue(), uploaded_doc.name, doc_type, "flash" if "Flash" in ai_mode_choice else "thinking"))
                st.rerun()

        with st.container(border=True):
            st.markdown(f'<div class="section-title">{t("hdr_title", doc_type=doc_type)}</div>', unsafe_allow_html=True)
            col_l, col_r = st.columns(2)
            with col_l:
                to_name = render_unified_input("To", st.session_state['doc_info'].get("to", ""), db_to_options, "to")
                attn_name = render_unified_input("Attention", st.session_state['doc_info'].get("attn", ""), [], "attn")
                your_ref = render_unified_input("Your Ref. No.", st.session_state['doc_info'].get("your_ref", ""), [], "your_ref")
                ship_name = render_unified_input("Ship's Name", st.session_state['doc_info'].get("ship", ""), db_ship_options, "ship")
            with col_r:
                pic_name = render_unified_input("PIC", st.session_state['doc_info'].get("pic", ""), [st.session_state.get('user_email', '')], "pic")
                date_str = render_unified_input("Date", st.session_state['doc_info'].get("date", ""), [datetime.now().strftime("%Y-%m-%d")], "date")
                our_ref = render_unified_input("Our Ref. No.", st.session_state['doc_info'].get("our_ref", ""), [], "our_ref")
                validity = render_unified_input("Validity", st.session_state['doc_info'].get("validity", ""), ["30 Days"], "validity")

            project_title = render_unified_input("Project Title", st.session_state['doc_info'].get("project_title", ""), [], "project_title")
            currency = render_unified_input("Currency", st.session_state['doc_info'].get("currency", ""), CURRENCY_OPTIONS, "currency") or "KRW"

            edited_df = st.data_editor(st.session_state['doc_items'], num_rows="dynamic", use_container_width=True)
            calc_total_val = edited_df["Amount"].apply(safe_float).sum()

            if st.button("🚀 서류 대장 및 DB 일괄 저장"):
                save_to_doc_ledger(OUR_DB_FILE, doc_type, your_ref, our_ref, ship_name, to_name, date_str, currency, calc_total_val, len(edited_df), st.session_state.get('user_email'))
                save_items_to_master(edited_df, supplier_name=to_name or "자사 생성", currency=currency)
                st.success("🎉 구글 시트 / DB에 성공적으로 저장되었습니다.")

    with right_col:
        with st.container(border=True):
            st.markdown(f'<div class="section-title">{t("preview_title")}</div>', unsafe_allow_html=True)
            preview_ctx = {
                "doc_title": doc_type.upper(), "to_name": to_name, "attn_name": attn_name, "project_title": project_title,
                "validity": validity, "flag_class": "", "our_ref": our_ref, "date_str": date_str,
                "pic": pic_name, "your_ref": your_ref, "ship_name": ship_name, "payment_due": "", "currency": currency,
                "items": prepare_items_for_pdf(clean_df(edited_df).to_dict("records"), currency=currency),
                "bottom_remarks": "", "total_amount_str": f"{get_currency_symbol(currency)}{calc_total_val:,.2f}"
            }
            realtime_pdf_bytes = generate_pdf(preview_ctx)
            st.download_button(t("btn_download_pdf"), realtime_pdf_bytes, file_name=f"{doc_type}.pdf", mime="application/pdf")
            for i, img_b in enumerate(render_pdf_images(realtime_pdf_bytes)): st.image(img_b, caption=f"Page {i+1}", use_container_width=True)

# ==========================================
# 7. 서류 관리 대장
# ==========================================
elif menu == "서류 관리 대장":
    st.markdown(f"""<div class="main-header"><h1>{t('doc_ledger_title')}</h1></div>""", unsafe_allow_html=True)
    tab_our, tab_cust = st.tabs(["🏢 자사 서류 대장", "🤝 고객사 / 공급사 서류 대장"])
    
    def render_ledger_tab(db_name):
        df = safe_read_db(db_name, doc_db_cols)
        edited = st.data_editor(df, num_rows="dynamic", use_container_width=True)
        if st.button("💾 대장 변경사항 저장", key=f"save_{db_name}"):
            safe_save_db(edited, db_name, doc_db_cols)
            st.success("🎉 성공적으로 동기화되었습니다.")

    with tab_our: render_ledger_tab(OUR_DB_FILE)
    with tab_cust: render_ledger_tab(CUSTOMER_DB_FILE)

# ==========================================
# 8. 자재 단가 마스터 DB
# ==========================================
elif menu == "자재 단가 마스터 DB":
    st.markdown(f"""<div class="main-header"><h1>{t('item_master_title')}</h1></div>""", unsafe_allow_html=True)
    df = safe_read_db(ITEM_MASTER_FILE, item_master_cols)
    edited = st.data_editor(df, num_rows="dynamic", use_container_width=True)
    if st.button("💾 자재 DB 변경사항 저장"):
        safe_save_db(edited, ITEM_MASTER_FILE, item_master_cols)
        st.success("🎉 자재 마스터 DB가 성공적으로 동기화되었습니다.")

# ==========================================
# 9. 관리자 메뉴
# ==========================================
elif menu == "관리자 메뉴":
    st.markdown("""<div class="main-header"><h1>🛠️ 관리자 메뉴</h1></div>""", unsafe_allow_html=True)
    pwd = st.text_input("관리자 비밀번호", type="password")
    if pwd == ADMIN_PASSWORD:
        st.success("🔓 관리자 권한 인증 완료")
        if st.button("🚨 전체 데이터 초기화"):
            safe_save_db(pd.DataFrame(columns=doc_db_cols), OUR_DB_FILE, doc_db_cols)
            safe_save_db(pd.DataFrame(columns=doc_db_cols), CUSTOMER_DB_FILE, doc_db_cols)
            safe_save_db(pd.DataFrame(columns=item_master_cols), ITEM_MASTER_FILE, item_master_cols)
            st.success("모든 DB가 초기화되었습니다.")

# ==========================================
# 10. 서류 이력
# ==========================================
else:
    st.markdown("""<div class="main-header"><h1>🖼️ 서류 이력</h1></div>""", unsafe_allow_html=True)
    st.info("발행 완료된 파일 목록을 조회합니다.")

if is_running:
    time.sleep(1.0)
    st.rerun()
