import streamlit as st
import pandas as pd
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
import os
import base64
import json
import re
import time
import io
import threading
import subprocess
from datetime import datetime
import google.generativeai as genai
from PIL import Image
from streamlit.runtime.scriptrunner import add_script_run_ctx

# ==========================================
# 0. 관리자 보안 및 API 키 영구 고정 설정
# ==========================================
ADMIN_PASSWORD = "admin1234"  # DB 초기화용 비밀번호
DEFAULT_GEMINI_KEY = ""       # API 키 입력 시 영구 적용 (Secrets 사용 시 빈칸 유지)

# ==========================================
# 1. 페이지 설정 & 고대비 CSS
# ==========================================
st.set_page_config(page_title="ONESOLUTION Enterprise ERP", layout="wide", page_icon="🚢")

custom_css = """
<style>
    .main-header {
        background: var(--secondary-background-color);
        border: 2px solid #0284C7; 
        border-left: 6px solid #0284C7;
        padding: 20px 24px; border-radius: 12px; margin-bottom: 24px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    }
    .main-header h1 { color: var(--text-color); font-size: 1.8rem; font-weight: 800; margin: 0; }
    .main-header p { color: var(--text-color); opacity: 0.85; margin: 6px 0 0 0; font-size: 0.95rem; font-weight: 500; }
    
    .erp-card { 
        background: var(--secondary-background-color); 
        border: 2px solid #0284C7; 
        border-radius: 12px; padding: 20px; margin-bottom: 20px; 
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    .section-title { color: #0284C7; font-size: 1.15rem; font-weight: 800; margin-bottom: 16px; }
    
    .stButton > button {
        width: 100%; background: linear-gradient(135deg, #1D4ED8 0%, #0284C7 100%) !important;
        color: #FFFFFF !important; font-weight: 700 !important; border: none !important;
        padding: 10px 20px !important; border-radius: 8px !important;
        font-size: 1rem !important;
    }
    .stButton > button:disabled {
        background: #64748B !important; color: #F1F5F9 !important; cursor: not-allowed !important;
    }
    
    .total-badge {
        background: var(--secondary-background-color); 
        border: 2px solid #0284C7; padding: 16px 20px; border-radius: 10px;
        text-align: right; font-size: 1.3rem; font-weight: 800; color: #0284C7; margin-top: 12px;
    }
    
    .loader-container {
        display: flex; align-items: center; justify-content: center;
        background: var(--secondary-background-color);
        border: 2px solid #0284C7; border-radius: 12px;
        padding: 20px; margin-bottom: 20px;
        box-shadow: 0 0 15px rgba(2, 132, 199, 0.25);
    }
    .spinner {
        border: 4px solid rgba(2, 132, 199, 0.2);
        border-top: 4px solid #0284C7;
        border-radius: 50%;
        width: 35px; height: 35px;
        animation: spin 1s linear infinite;
        margin-right: 15px;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .loader-text { color: var(--text-color); font-weight: 700; font-size: 1.1rem; }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# ==========================================
# 2. 백그라운드 태스크 및 환경 초기화
# ==========================================
KEY_FILE = "gemini_key.txt"
DB_FILE = "master_db.csv"
HISTORY_FILE = "master_history.json"
LEDGER_FILE = "doc_ledger.csv"
os.makedirs("output", exist_ok=True)

@st.cache_resource
def install_playwright_browser():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=False)
    except Exception:
        pass

install_playwright_browser()

if 'bg_task' not in st.session_state:
    st.session_state['bg_task'] = {
        'status': 'idle',   # idle, running, completed, error
        'type': None,       # doc_parse, db_parse
        'progress_msg': '',
        'result': None,
        'error_msg': None
    }

is_running = (st.session_state['bg_task']['status'] == 'running')

def load_saved_key():
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass
        
    if DEFAULT_GEMINI_KEY.strip(): return DEFAULT_GEMINI_KEY.strip()
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            k = f.read().strip()
            if k: return k
    return ""

gemini_key = load_saved_key()

# DB 초기화
if os.path.exists(DB_FILE):
    db_init = pd.read_csv(DB_FILE)
    if "Category" in db_init.columns and "PartNo" not in db_init.columns:
        db_init = db_init.rename(columns={"Category": "PartNo"})
    for req in ["PartNo", "ItemName", "Description", "UnitPrice", "Remarks"]:
        if req not in db_init.columns: db_init[req] = "" if req != "UnitPrice" else 0.0
    db_init = db_init[["PartNo", "ItemName", "Description", "UnitPrice", "Remarks"]]
    db_init.to_csv(DB_FILE, index=False)
else:
    pd.DataFrame(columns=["PartNo", "ItemName", "Description", "UnitPrice", "Remarks"]).to_csv(DB_FILE, index=False)

if not os.path.exists(LEDGER_FILE):
    pd.DataFrame(columns=["Date", "DocType", "YourRef", "OurRef", "ShipName", "TargetName", "Currency", "TotalAmount", "ItemCount"]).to_csv(LEDGER_FILE, index=False)

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

def save_to_ledger(doc_type, your_ref, our_ref, ship_name, target_name, date_str, currency, total_amount, item_count):
    ledger_df = pd.read_csv(LEDGER_FILE) if os.path.exists(LEDGER_FILE) else pd.DataFrame()
    new_entry = pd.DataFrame([{
        "Date": date_str or "-",
        "DocType": doc_type, "YourRef": your_ref or "-", "OurRef": our_ref or "-",
        "ShipName": ship_name or "-", "TargetName": target_name or "-",
        "Currency": currency or "-", "TotalAmount": total_amount, "ItemCount": item_count
    }])
    updated_ledger = pd.concat([ledger_df, new_entry], ignore_index=True)
    updated_ledger.to_csv(LEDGER_FILE, index=False)

def safe_merge_db(existing_db, new_data_df):
    if new_data_df is None or new_data_df.empty: return existing_db
    combined = pd.concat([existing_db, new_data_df], ignore_index=True)
    for col in ['PartNo', 'ItemName', 'Description', 'UnitPrice', 'Remarks']:
        if col not in combined.columns:
            combined[col] = '' if col != 'UnitPrice' else 0.0

    has_pno = combined['PartNo'].astype(str).str.strip() != ""
    has_item = combined['ItemName'].astype(str).str.strip() != ""
    has_desc = combined['Description'].astype(str).str.strip() != ""
    
    combined = combined[has_pno | has_item | has_desc]
    final_db = combined.drop_duplicates(subset=['PartNo', 'ItemName', 'Description'], keep='last')
    return final_db

if 'doc_info' not in st.session_state:
    st.session_state['doc_info'] = {
        "to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "",
        "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "",
        "currency": "KRW", "bottom_remarks": ""
    }

if 'doc_items' not in st.session_state:
    st.session_state['doc_items'] = pd.DataFrame([{
        "No": 1, "PartNo": "", "ItemName": "", "Description": "", "Qty": 1, "UnitPrice": 0.0, "Amount": 0.0, "Remarks": ""
    }])

# ⭐ 이중 안전 PDF 생성 엔진 (Playwright 오류 발생 시 xhtml2pdf 백업 자동 실행)
def generate_pdf(template_name, context):
    logo_path = os.path.abspath("logo.png")
    context["logo_base64"] = base64.b64encode(open(logo_path, "rb").read()).decode('utf-8') if os.path.exists(logo_path) else None
    
    try:
        html_out = Environment(loader=FileSystemLoader("templates")).get_template(template_name).render(context)
    except Exception:
        try:
            html_out = Environment(loader=FileSystemLoader(".")).get_template(template_name).render(context)
        except Exception:
            html_out = f"<h1>{context.get('doc_title','DOCUMENT')}</h1><pre>{json.dumps(context, indent=2, ensure_ascii=False)}</pre>"
    
    # 1차: Playwright 시도
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--no-zygote'
                ]
            )
            page = browser.new_page()
            page.set_content(html_out, wait_until="load")
            pdf_bytes = page.pdf(
                format="A4", 
                print_background=True, 
                margin={"top":"10mm","bottom":"10mm","left":"10mm","right":"10mm"}
            )
            browser.close()
            return pdf_bytes
    except Exception:
        # 2차 폴백: xhtml2pdf 경량 인메모리 변환 엔진
        try:
            from xhtml2pdf import pisa
            pdf_buffer = io.BytesIO()
            pisa.CreatePDF(html_out, dest=pdf_buffer)
            return pdf_buffer.getvalue()
        except Exception:
            return html_out.encode('utf-8')

def show_pdf_preview(pdf_bytes):
    st.markdown(f'<iframe src="data:application/pdf;base64,{base64.b64encode(pdf_bytes).decode("utf-8")}" width="100%" height="800px" style="border-radius:12px; border:2px solid #0284C7;"></iframe>', unsafe_allow_html=True)

def clean_str(val):
    if pd.isna(val) or val is None: return ""
    s = str(val).strip()
    return "" if s.lower() in ['nan', 'none', 'null', '<na>', 'nan.0'] else s

# ==========================================
# 3. AI 파싱 엔진
# ==========================================
def get_ai_response(api_key, content_list, mode="flash"):
    if not api_key: raise Exception("Gemini API Key가 누락되었습니다.")
    genai.configure(api_key=api_key)
    
    available_models = []
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
    except Exception:
        pass

    if available_models:
        if mode == "thinking":
            thinking_models = [m for m in available_models if any(k in m.lower() for k in ['thinking', 'pro', '2.5'])]
            other_models = [m for m in available_models if m not in thinking_models]
            candidate_models = thinking_models + other_models
        else:
            flash_models = [m for m in available_models if 'flash' in m.lower()]
            other_models = [m for m in available_models if m not in flash_models]
            candidate_models = flash_models + other_models
    else:
        candidate_models = ['models/gemini-1.5-flash', 'models/gemini-2.0-flash', 'models/gemini-1.5-pro']

    last_err = None
    for model_name in candidate_models:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(content_list)
            if response and response.text:
                res_text = response.text.strip()
                start_idx = res_text.find('[') if '[' in res_text and (res_text.find('[') < res_text.find('{') or '{' not in res_text) else res_text.find('{')
                end_idx = res_text.rfind(']') if ']' in res_text and (res_text.rfind(']') > res_text.rfind('}') or '}' not in res_text) else res_text.rfind('}')
                if start_idx != -1 and end_idx != -1: res_text = res_text[start_idx:end_idx + 1]
                return json.loads(res_text)
        except Exception as e:
            last_err = e
            continue
    raise Exception(f"AI 모델 호출 실패: {last_err}")

def run_bg_doc_parse(task_state, api_key, file_bytes, file_type, doc_type, ai_mode):
    try:
        task_state['status'] = 'running'
        mode_label = "Thinking(사고)" if ai_mode == "thinking" else "Flash(고속)"
        task_state['progress_msg'] = f'AI [{mode_label}] 엔진이 {doc_type} 문서를 분석 중입니다...'
        
        prompt = f"""
        Extract document details to match the required fixed header fields:
        
        FIXED HEADER FIELDS:
        1. "to_name": To (Client / Company Name)
        2. "attn_name": Attention (Person / Dept)
        3. "project_title": Project Title / Subject
        4. "validity": Validity e.g. "By Aug 21, 2026"
        5. "flag_class": Flag / Class e.g. "HONG KONG / NK"
        6. "our_ref": Our Ref. No.
        7. "date_str": Date (YYYY-MM-DD)
        8. "pic": PIC (Person In Charge)
        9. "your_ref": Your Ref. No.
        10. "ship_name": Ship's Name
        
        ITEM LIST RULES:
        - "PartNo": Part No
        - "ItemName": Main item name
        - "Description": Detailed description (Type, Model, Specs)
        - "Qty": Quantity
        - "UnitPrice": Price per unit
        - "Amount": Total amount for item (Qty * UnitPrice or explicitly stated)
        - "Remarks": Inline remarks
        
        Extract details into valid JSON:
        {{
            "to_name": "Company Name",
            "attn_name": "Contact Person",
            "project_title": "Project Title",
            "validity": "Validity",
            "flag_class": "Flag / Class",
            "our_ref": "Our Ref. No.",
            "date_str": "YYYY-MM-DD",
            "pic": "PIC Name",
            "your_ref": "Your Ref. No.",
            "ship_name": "Ship Name",
            "currency": "KRW/USD/EUR",
            "items": [{{"PartNo": "", "ItemName": "", "Description": "", "Qty": 1, "UnitPrice": 0.0, "Amount": 0.0, "Remarks": ""}}]
        }}
        Return ONLY raw JSON.
        """
        if file_type in ['png', 'jpg', 'jpeg']: 
            content = Image.open(io.BytesIO(file_bytes))
        else: 
            content = {"mime_type": "application/pdf", "data": file_bytes}
            
        ai_data = get_ai_response(api_key, [prompt, content], mode=ai_mode)
        task_state['result'] = {'doc_type': doc_type, 'ai_data': ai_data}
        task_state['status'] = 'completed'
    except Exception as e:
        task_state['status'] = 'error'
        task_state['error_msg'] = str(e)

def run_bg_sheet_parse(task_state, api_key, excel_bytes, sheet_names, ai_mode):
    try:
        task_state['status'] = 'running'
        all_results = []
        excel_file = pd.ExcelFile(io.BytesIO(excel_bytes))
        total = len(sheet_names)
        
        for idx, s_name in enumerate(sheet_names):
            mode_label = "Thinking(사고)" if ai_mode == "thinking" else "Flash(고속)"
            task_state['progress_msg'] = f"[{idx+1}/{total}] '{s_name}' 시트 [{mode_label}] 추출 중..."
            try:
                df_s = pd.read_excel(excel_file, sheet_name=s_name)
                df_clean = df_s.dropna(how='all').dropna(how='all', axis=1)
                if not df_clean.empty:
                    csv_str = df_clean.to_csv(index=False)
                    prompt = f"Extract ALL items from sheet '{s_name}' into JSON Array: [{{\"PartNo\":\"\", \"ItemName\":\"\", \"Description\":\"\", \"UnitPrice\":100.0, \"Remarks\":\"\"}}]."
                    res = get_ai_response(api_key, [prompt, f"CSV Content:\n{csv_str}"], mode=ai_mode)
                    if isinstance(res, list): all_results.extend(res)
            except Exception: pass
            
        parsed_df = pd.DataFrame(all_results)
        for col in ['PartNo', 'ItemName', 'Description', 'UnitPrice', 'Remarks']:
            if col not in parsed_df.columns: parsed_df[col] = '' if col != 'UnitPrice' else 0.0
        parsed_df['UnitPrice'] = pd.to_numeric(parsed_df['UnitPrice'], errors='coerce').fillna(0.0)
        
        task_state['result'] = parsed_df
        task_state['status'] = 'completed'
    except Exception as e:
        task_state['status'] = 'error'
        task_state['error_msg'] = str(e)

def start_bg_thread(target_func, args):
    t = threading.Thread(target=target_func, args=args)
    add_script_run_ctx(t)
    t.start()

# ==========================================
# 4. 사이드바 메뉴
# ==========================================
st.sidebar.title("🚢 ONESOLUTION ERP")

st.sidebar.markdown("""
    <div style="background: rgba(2, 132, 199, 0.1); border: 1px solid #0284C7; border-radius: 8px; padding: 10px 12px; text-align: center; margin-bottom: 20px;">
        <span style="color: #0284C7; font-size: 0.85rem; font-weight: 800;">✨ Powered by Google Gemini 3.6</span>
    </div>
""", unsafe_allow_html=True)

menu = st.sidebar.radio("SYSTEM MENU", ["서류 통합 생성", "서류 관리대장", "마스터 DB 관리", "발행 이력 조회"])

task = st.session_state['bg_task']
if is_running:
    st.markdown(f"""
        <div class="loader-container">
            <div class="spinner"></div>
            <div class="loader-text">{task['progress_msg']} <br>
            <span style='font-size:0.85rem; color:var(--text-color); opacity:0.75; font-weight:500;'>작업 중에도 다른 메뉴로 자유롭게 이동하실 수 있습니다.</span></div>
        </div>
    """, unsafe_allow_html=True)
elif task['status'] == 'error':
    st.error(f"❌ AI 작업 오류: {task['error_msg']}")

# ==========================================
# 5. 서류 통합 생성
# ==========================================
if menu == "서류 통합 생성":
    doc_type = st.sidebar.selectbox("📋 서류 유형 선택", ["Quotation", "Invoice", "Delivery Note", "Purchase Order", "Credit Note", "Service Report"])

    st.markdown(f"""
        <div class="main-header">
            <h1>📄 스마트 서류 자동 생성 시스템 ({doc_type})</h1>
            <p>AI 문서 분석을 기반으로 고정 양식 및 마스터 DB 연동 생성을 지원합니다.</p>
        </div>
    """, unsafe_allow_html=True)

    col_empty1, col_empty2 = st.columns([8, 2])
    with col_empty2:
        if st.button("🔄 서류 초기화", disabled=is_running):
            st.session_state['doc_info'] = {
                "to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "",
                "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "",
                "currency": "KRW", "bottom_remarks": ""
            }
            st.session_state['doc_items'] = pd.DataFrame([{
                "No": 1, "PartNo": "", "ItemName": "", "Description": "", "Qty": 1, "UnitPrice": 0.0, "Amount": 0.0, "Remarks": ""
            }])
            if 'last_pdf' in st.session_state: del st.session_state['last_pdf']
            if 'last_file' in st.session_state: del st.session_state['last_file']
            st.rerun()

    db = pd.read_csv(DB_FILE)

    if task['status'] == 'completed' and task['type'] == 'doc_parse':
        res_data = task['result']
        ai_data = res_data['ai_data']
        
        st.session_state['doc_info'] = {
            "to": ai_data.get("to_name", ""),
            "attn": ai_data.get("attn_name", ""),
            "project_title": ai_data.get("project_title", ""),
            "validity": ai_data.get("validity", ""),
            "flag_class": ai_data.get("flag_class", ""),
            "our_ref": ai_data.get("our_ref", ""),
            "date": ai_data.get("date_str", datetime.now().strftime("%Y-%m-%d")),
            "pic": ai_data.get("pic", ""),
            "your_ref": ai_data.get("your_ref", ""),
            "ship": ai_data.get("ship_name", ""),
            "currency": ai_data.get("currency", "KRW"),
            "bottom_remarks": st.session_state['doc_info'].get("bottom_remarks", "")
        }
        
        parsed_items = ai_data.get("items", [])
        items_df = pd.DataFrame(parsed_items) if parsed_items else pd.DataFrame()
        
        if not items_df.empty:
            items_df["No"] = range(1, len(items_df) + 1)
            for req_col in ["PartNo", "ItemName", "Description", "Qty", "UnitPrice", "Amount", "Remarks"]:
                if req_col not in items_df.columns:
                    items_df[req_col] = "" if req_col not in ["Qty", "UnitPrice", "Amount"] else (1 if req_col == "Qty" else 0.0)

            for idx, row in items_df.iterrows():
                pno = str(row.get('PartNo', ''))
                iname = str(row.get('ItemName', ''))
                desc = str(row.get('Description', ''))
                
                match = pd.DataFrame()
                if not db.empty:
                    if pno and 'PartNo' in db.columns: match = db[db['PartNo'] == pno]
                    if match.empty and iname and 'ItemName' in db.columns: match = db[db['ItemName'] == iname]
                    if match.empty and desc and 'Description' in db.columns: match = db[db['Description'] == desc]
                
                if not match.empty:
                    m = match.iloc[0]
                    if float(row.get('UnitPrice', 0.0)) == 0.0: items_df.at[idx, 'UnitPrice'] = float(m.get('UnitPrice', 0.0))
                    if not pno: items_df.at[idx, 'PartNo'] = str(m.get('PartNo', ''))
                    if not iname: items_df.at[idx, 'ItemName'] = str(m.get('ItemName', ''))
                    if not desc: items_df.at[idx, 'Description'] = str(m.get('Description', ''))
                
                if float(row.get('Amount', 0.0)) == 0.0:
                    items_df.at[idx, 'Amount'] = float(items_df.at[idx, 'Qty']) * float(items_df.at[idx, 'UnitPrice'])

            st.session_state['doc_items'] = items_df

        st.session_state['bg_task']['status'] = 'idle'
        st.success(f"✅ [{res_data.get('doc_type', doc_type)}] AI 분석 완료. 결과가 반영되었습니다.")

    with st.expander("🤖 AI 문서 분석", expanded=True):
        ai_mode_choice = st.radio("AI 분석 엔진 선택", ["⚡ 고속 Flash 모드 (일반 권장)", "🧠 심층 Thinking (사고) 모드 (복잡한 문서 권장)"], horizontal=True, disabled=is_running)
        selected_mode = "thinking" if "Thinking" in ai_mode_choice else "flash"

        uploaded_doc = st.file_uploader("문서 업로드 (PDF, JPG, PNG)", type=["pdf", "png", "jpg", "jpeg"], disabled=is_running)
        if uploaded_doc:
            if st.button("✨ AI 문서 분석", disabled=is_running):
                st.session_state['bg_task']['type'] = 'doc_parse'
                ext = uploaded_doc.name.split('.')[-1].lower()
                doc_bytes = uploaded_doc.getvalue()
                start_bg_thread(run_bg_doc_parse, (st.session_state['bg_task'], gemini_key, doc_bytes, ext, doc_type, selected_mode))
                st.rerun()

    history = load_history()

    st.markdown('<div class="erp-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-title">📌 {doc_type} 고정 헤더 정보 (Fixed Header)</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    with col1:
        sel_to = st.selectbox("To (수신처 선택)", options=[""] + history["to_list"])
        to_name = st.text_input("To", value=st.session_state['doc_info']["to"] if not sel_to else sel_to)

        sel_attn = st.selectbox("Attention (참조/담당자 선택)", options=[""] + history["attns"])
        attn_name = st.text_input("Attention", value=st.session_state['doc_info']["attn"] if not sel_attn else sel_attn)

        project_title = st.text_input("Project Title", value=st.session_state['doc_info'].get("project_title", ""))
        validity = st.text_input("Validity", value=st.session_state['doc_info'].get("validity", ""))
        flag_class = st.text_input("Flag / Class", value=st.session_state['doc_info'].get("flag_class", ""))

    with col2:
        our_ref = st.text_input("Our Ref. No.", value=st.session_state['doc_info'].get("our_ref", ""))
        date_str = st.text_input("Date", value=st.session_state['doc_info'].get("date", datetime.now().strftime("%Y-%m-%d")))
        pic_name = st.text_input("PIC", value=st.session_state['doc_info'].get("pic", ""))
        your_ref = st.text_input("Your Ref. No.", value=st.session_state['doc_info'].get("your_ref", ""))
        
        sel_ship = st.selectbox("Ship's Name (선박명 선택)", options=[""] + history["ships"])
        ship_name = st.text_input("Ship's Name", value=st.session_state['doc_info']["ship"] if not sel_ship else sel_ship)

        curr_val = st.session_state['doc_info'].get("currency", "KRW")
        curr_opts = ["KRW", "USD", "EUR"]
        curr_idx = curr_opts.index(curr_val) if curr_val in curr_opts else 0
        currency = st.selectbox("Currency (통화)", curr_opts, index=curr_idx)

    if doc_type == "Service Report":
        fault = st.text_area("A. Fault Reported")
        action = st.text_area("B. Action Taken")
        result = st.text_area("C. Result")
    
    st.markdown('<div class="section-title" style="margin-top:20px;">📦 품목 상세 내역 (Amount 수기 수정 가능)</div>', unsafe_allow_html=True)
    
    if not db.empty:
        with st.expander("🔍 과거 등록 자재 DB 검색 및 1-Click 스펙 매칭 도우미", expanded=False):
            search_kw = st.text_input("검색할 자재명/스펙 입력 (예: Magnetron, Valve, O-Ring)", value="")
            if search_kw.strip():
                cond1 = db['ItemName'].astype(str).str.contains(search_kw.strip(), case=False, na=False) if 'ItemName' in db.columns else False
                cond2 = db['Description'].astype(str).str.contains(search_kw.strip(), case=False, na=False) if 'Description' in db.columns else False
                matched_db = db[cond1 | cond2]
                
                if not matched_db.empty:
                    st.write(f"💡 '{search_kw}' 검색 결과 ({len(matched_db)}건 발견):")
                    opts = [f"PartNo: {r['PartNo']} | 품목명: {r.get('ItemName','')} | 상세: {r['Description']} | 단가: {r['UnitPrice']:,.0f}" for _, r in matched_db.iterrows()]
                    selected_match = st.selectbox("적용할 과거 자재 스펙을 선택하세요", ["선택 안함"] + opts)
                    
                    if selected_match != "선택 안함":
                        target_idx = opts.index(selected_match) - 1
                        chosen_row = matched_db.iloc[target_idx]
                        target_row_num = st.number_input("아래 품목 테이블의 몇 번째 행(No)에 적용할까요?", min_value=1, max_value=max(len(st.session_state['doc_items']), 1), value=1)
                        
                        if st.button("✨ 선택한 스펙을 해당 행에 반영하기"):
                            r_idx = target_row_num - 1
                            if r_idx < len(st.session_state['doc_items']):
                                st.session_state['doc_items'].at[r_idx, 'PartNo'] = str(chosen_row.get('PartNo', ''))
                                st.session_state['doc_items'].at[r_idx, 'ItemName'] = str(chosen_row.get('ItemName', ''))
                                st.session_state['doc_items'].at[r_idx, 'Description'] = str(chosen_row.get('Description', ''))
                                u_price = float(chosen_row.get('UnitPrice', 0.0))
                                qty_val = float(st.session_state['doc_items'].at[r_idx, 'Qty']) if 'Qty' in st.session_state['doc_items'].columns else 1.0
                                st.session_state['doc_items'].at[r_idx, 'UnitPrice'] = u_price
                                st.session_state['doc_items'].at[r_idx, 'Amount'] = u_price * qty_val
                                if 'Remarks' in st.session_state['doc_items'].columns:
                                    st.session_state['doc_items'].at[r_idx, 'Remarks'] = str(chosen_row.get('Remarks', ''))
                                st.success(f"No.{target_row_num} 행에 {chosen_row.get('ItemName', chosen_row.get('Description'))} 스펙이 정확히 반영되었습니다!")
                                st.rerun()
                else:
                    st.info("검색된 자재 이력이 없습니다.")

    item_name_list = db["ItemName"].dropna().unique().tolist() if not db.empty and "ItemName" in db.columns else []
    column_config = {
        "ItemName": st.column_config.SelectboxColumn("Item Name (품목명)", options=item_name_list, width="medium") if item_name_list else st.column_config.TextColumn("Item Name", width="medium"),
        "Description": st.column_config.TextColumn("Description (Model, Type 등 상세규격)", width="large"),
        "Qty": st.column_config.NumberColumn("Q'ty", format="%,d", min_value=1),
        "UnitPrice": st.column_config.NumberColumn("Unit Price", format="%,d", min_value=0),
        "Amount": st.column_config.NumberColumn("Amount (Net Price - 수기 수정 가능)", format="%,d", min_value=0),
        "Remarks": st.column_config.TextColumn("Remarks (비고)", width="medium"),
    }

    df_current = st.session_state['doc_items'].copy()
    for c in ["PartNo", "ItemName", "Description", "Qty", "UnitPrice", "Amount", "Remarks"]:
        if c not in df_current.columns:
            df_current[c] = "" if c not in ["Qty", "UnitPrice", "Amount"] else (1 if c == "Qty" else 0.0)

    for i, row in df_current.iterrows():
        if (float(row.get('Amount', 0.0)) == 0.0) and (float(row.get('UnitPrice', 0.0)) > 0):
            df_current.at[i, 'Amount'] = float(row.get('Qty', 1)) * float(row.get('UnitPrice', 0.0))

    edited_df = st.data_editor(df_current, column_config=column_config, num_rows="dynamic", use_container_width=True)

    for i, row in edited_df.iterrows():
        if pd.notna(row.get('ItemName')) and row['ItemName'] in db['ItemName'].values:
            match_row = db[db['ItemName'] == row['ItemName']].iloc[0]
            if not row.get('PartNo') or pd.isna(row.get('PartNo')):
                edited_df.at[i, 'PartNo'] = clean_str(match_row.get('PartNo', ''))
            if not row.get('Description') or pd.isna(row.get('Description')):
                edited_df.at[i, 'Description'] = clean_str(match_row.get('Description', ''))
            if row.get('UnitPrice', 0.0) == 0.0 or pd.isna(row.get('UnitPrice')):
                u_p = float(match_row.get('UnitPrice', 0.0))
                edited_df.at[i, 'UnitPrice'] = u_p
                if edited_df.at[i, 'Amount'] == 0.0:
                    edited_df.at[i, 'Amount'] = u_p * float(row.get('Qty', 1))

    if "Amount" in edited_df.columns:
        total_val = pd.to_numeric(edited_df["Amount"], errors='coerce').fillna(0).sum()
        disp_curr = currency if currency else "KRW"
        st.markdown(f'<div class="total-badge">Total Amount: {disp_curr} {total_val:,.2f}</div>', unsafe_allow_html=True)
    else:
        total_val = 0.0

    st.markdown('<div class="section-title" style="margin-top:20px;">📝 Remarks & Deviations (하단 서류 특기사항 - 수기 입력)</div>', unsafe_allow_html=True)
    bottom_remarks = st.text_area("하단 비고란 (PDF 서류 하단 [Remarks & Deviations] 영역에 직접 반영됩니다)", value=st.session_state['doc_info'].get("bottom_remarks", ""), height=100)

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="section-title">📌 작업 실행</div>', unsafe_allow_html=True)
    col_act1, col_act2 = st.columns(2)
    
    with col_act1:
        if st.button("📥 관리대장 및 마스터 DB 등록", type="secondary", disabled=is_running):
            st.session_state['doc_info'] = {
                "to": to_name, "attn": attn_name, "project_title": project_title, "validity": validity,
                "flag_class": flag_class, "our_ref": our_ref, "date": date_str, "pic": pic_name,
                "your_ref": your_ref, "ship": ship_name, "currency": currency, "bottom_remarks": bottom_remarks
            }
            st.session_state['doc_items'] = edited_df

            db_cols = ['PartNo', 'ItemName', 'Description', 'UnitPrice', 'Remarks']
            db_items = edited_df[[c for c in db_cols if c in edited_df.columns]].copy()
            for c in db_cols:
                if c not in db_items.columns: db_items[c] = '' if c != 'UnitPrice' else 0.0
            db_items['UnitPrice'] = pd.to_numeric(db_items['UnitPrice'], errors='coerce').fillna(0.0)
            
            updated_db = safe_merge_db(db, db_items)
            updated_db.to_csv(DB_FILE, index=False)

            save_to_ledger(doc_type, your_ref, our_ref, ship_name, to_name, date_str, currency, total_val, len(edited_df))
            save_history(ship_name, to_name, attn_name)
            
            st.success("🎉 서류 관리대장 및 마스터 DB 등록 완료")

    with col_act2:
        if st.button(f"⚡ {doc_type} PDF 생성", type="primary", disabled=is_running):
            st.session_state['doc_info'] = {
                "to": to_name, "attn": attn_name, "project_title": project_title, "validity": validity,
                "flag_class": flag_class, "our_ref": our_ref, "date": date_str, "pic": pic_name,
                "your_ref": your_ref, "ship": ship_name, "currency": currency, "bottom_remarks": bottom_remarks
            }
            st.session_state['doc_items'] = edited_df
            save_history(ship_name, to_name, attn_name)

            ctx = {
                "doc_title": doc_type.upper(),
                "to_name": to_name,
                "attn_name": attn_name,
                "project_title": project_title,
                "validity": validity,
                "flag_class": flag_class,
                "our_ref": our_ref,
                "date_str": date_str or datetime.now().strftime("%Y-%m-%d"),
                "pic": pic_name,
                "your_ref": your_ref,
                "ship_name": ship_name,
                "currency": currency or "KRW",
                "items": edited_df.to_dict("records"),
                "total_amount": total_val,
                "bottom_remarks": bottom_remarks
            }
            if doc_type == "Service Report":
                ctx.update({"fault": fault, "action": action, "result": result, "order_by": to_name, "owner_agent": attn_name})

            template_map = {
                "Quotation": "invoice_po.html", "Delivery Note": "delivery_note.html",
                "Service Report": "service_report.html", "Invoice": "invoice_po.html",
                "Purchase Order": "invoice_po.html", "Credit Note": "invoice_po.html"
            }
            
            pdf = generate_pdf(template_map[doc_type], ctx)
            file_n = f"{doc_type}_{our_ref or your_ref}.pdf"
            with open(f"output/{file_n}", "wb") as f: f.write(pdf)
            
            st.session_state['last_pdf'] = pdf
            st.session_state['last_file'] = file_n

    if 'last_pdf' in st.session_state:
        st.markdown("---")
        st.download_button("💾 PDF 문서 다운로드", st.session_state['last_pdf'], file_name=st.session_state['last_file'], mime="application/pdf")
        show_pdf_preview(st.session_state['last_pdf'])

# ==========================================
# 6. 서류 관리대장
# ==========================================
elif menu == "서류 관리대장":
    st.markdown("""
        <div class="main-header">
            <h1>📑 서류 통합 관리대장</h1>
            <p>발행 및 등록된 모든 서류 이력 대장입니다.</p>
        </div>
    """, unsafe_allow_html=True)

    ledger_df = pd.read_csv(LEDGER_FILE) if os.path.exists(LEDGER_FILE) else pd.DataFrame()
    
    st.markdown('<div class="erp-card">', unsafe_allow_html=True)
    if not ledger_df.empty:
        col_f1, col_f2 = st.columns(2)
        with col_f1: filter_doc = st.selectbox("서류 유형 필터", ["전체"] + ledger_df["DocType"].dropna().unique().tolist())
        with col_f2: filter_ship = st.selectbox("선박명 필터", ["전체"] + ledger_df["ShipName"].dropna().unique().tolist())

        filtered_df = ledger_df.copy()
        if filter_doc != "전체": filtered_df = filtered_df[filtered_df["DocType"] == filter_doc]
        if filter_ship != "전체": filtered_df = filtered_df[filtered_df["ShipName"] == filter_ship]

        st.dataframe(filtered_df, use_container_width=True)
        csv_data = filtered_df.to_csv(index=False, encoding='utf-8-sig')
        st.download_button("📥 관리대장 엑셀(CSV) 다운로드", csv_data, file_name=f"document_ledger_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")
    else:
        st.info("등록된 서류 관리대장 이력이 없습니다.")
    st.markdown('</div>', unsafe_allow_html=True)

# ==========================================
# 7. 마스터 DB 관리
# ==========================================
elif menu == "마스터 DB 관리":
    st.header("🗄️ 마스터 데이터베이스(DB) 관리")
    db = pd.read_csv(DB_FILE)

    if task['status'] == 'completed' and task['type'] == 'db_parse':
        st.session_state['temp_db_upload'] = task['result']
        st.session_state['bg_task']['status'] = 'idle'
        st.success("🎉 시트 AI 파싱 완료")

    st.markdown('<div class="erp-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">🤖 AI 단가표 수집기</div>', unsafe_allow_html=True)
    
    ai_mode_choice_db = st.radio("AI 분석 엔진 선택", ["⚡ 고속 Flash 모드 (일반 권장)", "🧠 심층 Thinking (사고) 모드 (복잡한 시트 권장)"], horizontal=True, disabled=is_running, key="db_ai_mode")
    selected_mode_db = "thinking" if "Thinking" in ai_mode_choice_db else "flash"

    uploaded_db_file = st.file_uploader("단가표/자재 목록 파일 업로드 (xlsx, csv, pdf, jpg, png)", type=["xlsx", "csv", "pdf", "jpg", "png", "jpeg"], disabled=is_running)
    
    if uploaded_db_file:
        ext = uploaded_db_file.name.split('.')[-1].lower()
        if ext in ['xlsx', 'xls']:
            excel_file = pd.ExcelFile(uploaded_db_file)
            sheet_names = excel_file.sheet_names
            
            parse_mode = st.radio("파싱 모드 선택", ["📌 특정 시트 선택", "🚀 전체 시트 파싱"], horizontal=True, disabled=is_running)
            
            if parse_mode == "📌 특정 시트 선택":
                selected_sheet = st.selectbox("파싱할 시트를 선택하세요", sheet_names, disabled=is_running)
                if st.button("✨ 선택 시트 AI 분석", disabled=is_running):
                    st.session_state['bg_task']['type'] = 'db_parse'
                    db_bytes = uploaded_db_file.getvalue()
                    start_bg_thread(run_bg_sheet_parse, (st.session_state['bg_task'], gemini_key, db_bytes, [selected_sheet], selected_mode_db))
                    st.rerun()
            else:
                if st.button("🚀 전체 시트 AI 파싱 시작", disabled=is_running):
                    st.session_state['bg_task']['type'] = 'db_parse'
                    db_bytes = uploaded_db_file.getvalue()
                    start_bg_thread(run_bg_sheet_parse, (st.session_state['bg_task'], gemini_key, db_bytes, sheet_names, selected_mode_db))
                    st.rerun()

    if 'temp_db_upload' in st.session_state and not st.session_state['temp_db_upload'].empty:
        st.markdown("<br>", unsafe_allow_html=True)
        st.write(f"🔍 **AI 추출 검토 데이터 ({len(st.session_state['temp_db_upload'])}개 항목)**")
        st.dataframe(st.session_state['temp_db_upload'], use_container_width=True)
        
        if st.button("✅ 위 목록을 DB에 최종 통합 저장", disabled=is_running):
            updated_db = safe_merge_db(db, st.session_state['temp_db_upload'])
            updated_db.to_csv(DB_FILE, index=False)
            del st.session_state['temp_db_upload']
            st.success(f"성공적으로 마스터 DB에 통합 저장되었습니다! (총 {len(updated_db)}개 자재 저장됨)")
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown('<div class="erp-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📊 DB 데이터 목록</div>', unsafe_allow_html=True)
    edited_db = st.data_editor(db, num_rows="dynamic", use_container_width=True, disabled=is_running)
    if st.button("💾 DB 수정사항 저장", disabled=is_running):
        edited_db.to_csv(DB_FILE, index=False)
        st.success("저장되었습니다.")
    st.markdown('</div>', unsafe_allow_html=True)

    # 초기화
    st.markdown('<div class="erp-card">', unsafe_allow_html=True)
    with st.expander("🚨 관리자 전용: DB 및 이력 전체 초기화"):
        st.warning("⚠️ 초기화 시 저장된 모든 자재 DB, 서류 관리대장, 자동완성 이력이 삭제됩니다.")
        pwd_input = st.text_input("관리자 비밀번호 입력", type="password", key="reset_pwd", disabled=is_running)
        confirm_check = st.checkbox("모든 데이터를 초기화하는 것에 동의합니다.", disabled=is_running)
        if st.button("🔥 DB 전체 초기화 실행", disabled=is_running):
            if pwd_input == ADMIN_PASSWORD and confirm_check:
                pd.DataFrame(columns=["PartNo", "ItemName", "Description", "UnitPrice", "Remarks"]).to_csv(DB_FILE, index=False)
                pd.DataFrame(columns=["Date", "DocType", "YourRef", "OurRef", "ShipName", "TargetName", "Currency", "TotalAmount", "ItemCount"]).to_csv(LEDGER_FILE, index=False)
                with open(HISTORY_FILE, "w", encoding="utf-8") as f: json.dump({"ships": [], "to_list": [], "attns": []}, f, ensure_ascii=False, indent=2)
                st.session_state['doc_info'] = {
                    "to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "",
                    "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "",
                    "currency": "KRW", "bottom_remarks": ""
                }
                st.session_state['doc_items'] = pd.DataFrame([{
                    "No": 1, "PartNo": "", "ItemName": "", "Description": "", "Qty": 1, "UnitPrice": 0.0, "Amount": 0.0, "Remarks": ""
                }])
                st.success("초기화되었습니다.")
                st.rerun()
            elif pwd_input != ADMIN_PASSWORD: st.error("❌ 비밀번호가 불일치합니다.")
            elif not confirm_check: st.error("⚠️ 동의 체크박스를 확인하세요.")
    st.markdown('</div>', unsafe_allow_html=True)

# ==========================================
# 8. 발행 이력 조회
# ==========================================
else:
    st.header("📁 서류 발행 이력")
    files = [f for f in os.listdir("output") if f.endswith('.pdf')]
    if files:
        selected_file = st.selectbox("문서 선택", files)
        if selected_file:
            pdf_data = open(os.path.join("output", selected_file), "rb").read()
            st.download_button("💾 다운로드", pdf_data, file_name=selected_file, mime="application/pdf")
            show_pdf_preview(pdf_data)

# ==========================================
# 9. 메인 스레드 안전 폴링
# ==========================================
if is_running:
    time.sleep(1.0)
    st.rerun()
