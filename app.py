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
from datetime import datetime
import google.generativeai as genai
from PIL import Image
from streamlit.runtime.scriptrunner import add_script_run_ctx

# ==========================================
# 0. 관리자 보안 및 API 키 설정
# ==========================================
ADMIN_PASSWORD = "admin1234"
DEFAULT_GEMINI_KEY = ""

FLAG_OPTIONS = ["선택 안함", "Panama", "Liberia", "Marshall Islands", "Hong Kong", "Singapore", "Korea (KR)", "Bahamas", "Malta", "Cyprus", "India", "China", "Greece", "UK"]

# ⭐ 중복 없이 완벽히 정렬된 선급(Class) 드롭다운 목록
CLASS_OPTIONS = [
    "선택 안함", "ABS", "BV", "CCS", "CRS", "DNV", "IRS", "KR", "LR", 
    "NK", "PRS", "RINA", "TL", "Non-IACS", "KR & NK", "DNV & LR", "IRS & DNV", "Panama / KR"
]

# ==========================================
# 1. 페이지 설정 & CSS
# ==========================================
st.set_page_config(page_title="ONE - ERP", layout="wide", page_icon="🚢")

custom_css = """
<style>
    .main-header { background: var(--secondary-background-color); border: 2px solid #0284C7; border-left: 6px solid #0284C7; padding: 16px 20px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
    .main-header h1 { color: var(--text-color); font-size: 1.5rem; font-weight: 800; margin: 0; }
    .main-header p { color: var(--text-color); opacity: 0.85; margin: 4px 0 0 0; font-size: 0.85rem; font-weight: 500; }
    .erp-card { background: var(--secondary-background-color); border: 2px solid #0284C7; border-radius: 12px; padding: 16px; margin-bottom: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
    .section-title { color: #0284C7; font-size: 1.05rem; font-weight: 800; margin-bottom: 12px; }
    .stButton > button { width: 100%; background: linear-gradient(135deg, #1D4ED8 0%, #0284C7 100%) !important; color: #FFFFFF !important; font-weight: 700 !important; border: none !important; padding: 8px 16px !important; border-radius: 8px !important; font-size: 0.95rem !important; }
    .stButton > button:disabled { background: #64748B !important; color: #F1F5F9 !important; cursor: not-allowed !important; }
    .total-badge { background: var(--secondary-background-color); border: 2px solid #0284C7; padding: 12px 16px; border-radius: 10px; text-align: right; font-size: 1.15rem; font-weight: 800; color: #0284C7; margin-top: 10px; }
    .loader-container { display: flex; align-items: center; justify-content: center; background: var(--secondary-background-color); border: 2px solid #0284C7; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    .spinner { border: 4px solid rgba(2, 132, 199, 0.2); border-top: 4px solid #0284C7; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin-right: 12px; }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .loader-text { color: var(--text-color); font-weight: 700; font-size: 1rem; }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# ==========================================
# 2. 내장형 PDF HTML 템플릿 (인쇄 여백 최소화 5mm)
# ==========================================
INLINE_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
    @page { size: A4; margin: 5mm 5mm; }
    body { font-family: 'Helvetica', 'Arial', sans-serif; font-size: 8.5pt; line-height: 1.25; color: #000; }
    .title { text-align: center; font-size: 20pt; font-weight: bold; text-decoration: underline; margin-bottom: 14px; text-transform: uppercase; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
    th, td { border: 1.5px solid #000; padding: 4px 6px; vertical-align: middle; }
    .hdr-label { width: 15%; font-weight: bold; font-size: 8.5pt; background-color: #f4f4f4; }
    .hdr-value { width: 35%; font-size: 8.5pt; }
    .currency { text-align: right; font-weight: bold; font-style: italic; margin-bottom: 4px; font-size: 8.5pt; }
    .item-th { font-weight: bold; text-align: center; background-color: #f4f4f4; font-size: 8.5pt; }
    .col-no { width: 5%; text-align: center; }
    .col-desc { width: 55%; }
    .col-qty { width: 8%; text-align: center; }
    .col-price { width: 16%; text-align: right; }
    .col-amt { width: 16%; text-align: right; }
    .remarks-box { border: 1.5px solid #000; padding: 8px; min-height: 60px; margin-top: 8px; font-size: 8.5pt; }
</style>
</head>
<body>
    {% if logo_base64 %}
    <div style="text-align: left; margin-bottom: 10px;">
        <img src="data:image/png;base64,{{ logo_base64 }}" style="max-height: 55px;" />
    </div>
    {% endif %}
    
    <div class="title">{{ doc_title }}</div>
    
    <table>
        <tr>
            <td class="hdr-label">To</td><td class="hdr-value">{{ to_name }}</td>
            <td class="hdr-label">Our Ref. No.</td><td class="hdr-value">{{ our_ref }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Attention</td><td class="hdr-value">{{ attn_name }}</td>
            <td class="hdr-label">Date</td><td class="hdr-value">{{ date_str }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Project Title</td><td class="hdr-value" colspan="3">{{ project_title }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Validity</td><td class="hdr-value">{{ validity }}</td>
            <td class="hdr-label">Your Ref. No.</td><td class="hdr-value">{{ your_ref }}</td>
        </tr>
        <tr>
            <td class="hdr-label">PIC</td><td class="hdr-value">{{ pic }}</td>
            <td class="hdr-label">Payment Due</td><td class="hdr-value">{{ payment_due }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Ship's Name</td><td class="hdr-value">{{ ship_name }}</td>
            <td class="hdr-label">Flag / Class</td><td class="hdr-value">{{ flag_class }}</td>
        </tr>
    </table>

    <div class="currency">Currency: {{ currency }}</div>
    
    <table>
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
                <td class="col-desc">
                    {% if item.ItemName %}<strong>{{ item.ItemName }}</strong><br>{% endif %}
                    {% if item.Description %}{{ item.Description | replace('\n', '<br>') }}{% endif %}
                    {% if item.Remarks %}<br><span style="font-size: 8pt; color: #444;"><em>{{ item.Remarks }}</em></span>{% endif %}
                </td>
                <td class="col-qty">{{ item.Qty }}</td>
                <td class="col-price">{{ item.UnitPriceFormatted }}</td>
                <td class="col-amt">{{ item.AmountFormatted }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    {% if bottom_remarks %}
    <div class="remarks-box">
        <strong>[Remarks & Deviations]</strong><br>
        {{ bottom_remarks | replace('\n', '<br>') }}
    </div>
    {% endif %}
</body>
</html>
"""

# ==========================================
# 3. 환경 및 데이터 정제 도구
# ==========================================
KEY_FILE = "gemini_key.txt"
DB_FILE = "master_db.csv"
HISTORY_FILE = "master_history.json"
LEDGER_FILE = "doc_ledger.csv"
os.makedirs("output", exist_ok=True)

def clean_df(df):
    if df is None or df.empty: return df
    df = df.copy().fillna("")
    for col in df.columns:
        df[col] = df[col].astype(str).replace(["nan", "NaN", "None", "null", "<NA>"], "")
    return df

def prepare_items_for_pdf(items_list):
    formatted_items = []
    for item in items_list:
        item_copy = dict(item)
        iname = str(item_copy.get('ItemName', '')).strip()
        desc = str(item_copy.get('Description', '')).strip()
        rem = str(item_copy.get('Remarks', '')).strip()
        
        if iname.lower() in ['nan', 'none', 'null', '<na>']: iname = ""
        if desc.lower() in ['nan', 'none', 'null', '<na>']: desc = ""
        if rem.lower() in ['nan', 'none', 'null', '<na>']: rem = ""
        
        item_copy['ItemName'] = iname
        item_copy['Description'] = desc
        item_copy['Remarks'] = rem
        
        u_p = item_copy.get('UnitPrice', 0)
        try:
            u_p_val = float(str(u_p).replace(',', '').strip()) if str(u_p).strip() else 0.0
        except (ValueError, TypeError): u_p_val = 0.0
            
        amt = item_copy.get('Amount', 0)
        try:
            amt_val = float(str(amt).replace(',', '').strip()) if str(amt).strip() else 0.0
        except (ValueError, TypeError): amt_val = 0.0
            
        item_copy['UnitPriceFormatted'] = f"{u_p_val:,.0f}" if u_p_val > 0 else ""
        if amt_val > 0:
            item_copy['AmountFormatted'] = f"{amt_val:,.0f}"
        elif amt_val == 0 and u_p_val > 0:
            item_copy['AmountFormatted'] = "0"
        else:
            item_copy['AmountFormatted'] = ""
            
        formatted_items.append(item_copy)
    return formatted_items

if 'bg_task' not in st.session_state:
    st.session_state['bg_task'] = {'status': 'idle', 'type': None, 'progress_msg': '', 'result': None, 'error_msg': None}

is_running = (st.session_state['bg_task']['status'] == 'running')

def load_saved_key():
    try:
        if "GEMINI_API_KEY" in st.secrets: return st.secrets["GEMINI_API_KEY"]
    except Exception: pass
    if DEFAULT_GEMINI_KEY.strip(): return DEFAULT_GEMINI_KEY.strip()
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            k = f.read().strip()
            if k: return k
    return ""

gemini_key = load_saved_key()

if os.path.exists(DB_FILE):
    db_init = pd.read_csv(DB_FILE)
    if "Category" in db_init.columns and "PartNo" not in db_init.columns: db_init = db_init.rename(columns={"Category": "PartNo"})
    for req in ["PartNo", "ItemName", "Description", "UnitPrice", "Remarks"]:
        if req not in db_init.columns: db_init[req] = "" if req != "UnitPrice" else 0.0
    clean_df(db_init[["PartNo", "ItemName", "Description", "UnitPrice", "Remarks"]]).to_csv(DB_FILE, index=False)
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
        "Date": date_str or "-", "DocType": doc_type, "YourRef": your_ref or "-", "OurRef": our_ref or "-",
        "ShipName": ship_name or "-", "TargetName": target_name or "-", "Currency": currency or "-", "TotalAmount": total_amount, "ItemCount": item_count
    }])
    pd.concat([ledger_df, new_entry], ignore_index=True).to_csv(LEDGER_FILE, index=False)

def safe_merge_db(existing_db, new_data_df):
    if new_data_df is None or new_data_df.empty: return existing_db
    combined = pd.concat([existing_db, new_data_df], ignore_index=True)
    for col in ['PartNo', 'ItemName', 'Description', 'UnitPrice', 'Remarks']:
        if col not in combined.columns: combined[col] = '' if col != 'UnitPrice' else 0.0
    has_pno = combined['PartNo'].astype(str).str.strip() != ""
    has_item = combined['ItemName'].astype(str).str.strip() != ""
    has_desc = combined['Description'].astype(str).str.strip() != ""
    res = combined[has_pno | has_item | has_desc].drop_duplicates(subset=['PartNo', 'ItemName', 'Description'], keep='last')
    return clean_df(res)

if 'doc_info' not in st.session_state:
    st.session_state['doc_info'] = {"to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "", "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "", "payment_due": "", "currency": "KRW", "bottom_remarks": ""}

if 'doc_items' not in st.session_state:
    st.session_state['doc_items'] = pd.DataFrame([{"No": 1, "PartNo": "", "ItemName": "", "Description": "", "Qty": 1, "UnitPrice": 0.0, "Amount": 0.0, "Remarks": ""}])

def generate_pdf(context):
    from weasyprint import HTML
    logo_path = os.path.abspath("logo.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f: context["logo_base64"] = base64.b64encode(f.read()).decode('utf-8')
    else: context["logo_base64"] = None
    
    env = Environment()
    template = env.from_string(INLINE_HTML_TEMPLATE)
    html_out = template.render(context)
    
    pdf_bytes = HTML(string=html_out).write_pdf()
    return pdf_bytes

def render_pdf_images(pdf_bytes):
    images = []
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            images.append(pix.tobytes("png"))
    except Exception: pass
    return images

def clean_str(val):
    if pd.isna(val) or val is None: return ""
    s = str(val).strip()
    return "" if s.lower() in ['nan', 'none', 'null', '<na>', 'nan.0'] else s

# ==========================================
# 4. AI 파싱 엔진
# ==========================================
def get_ai_response(api_key, content_list, mode="flash"):
    if not api_key: raise Exception("Gemini API Key가 누락되었습니다.")
    genai.configure(api_key=api_key)
    
    available_models = []
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
    except Exception: pass

    if available_models:
        if mode == "thinking":
            candidate_models = [m for m in available_models if any(k in m.lower() for k in ['thinking', 'pro', '2.5'])] + available_models
        else:
            candidate_models = [m for m in available_models if 'flash' in m.lower()] + available_models
    else:
        candidate_models = ['models/gemini-1.5-flash', 'models/gemini-2.0-flash', 'models/gemini-1.5-pro']

    last_err = None
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
            continue
    raise Exception(f"AI 모델 호출 실패: {last_err}")

def run_bg_doc_parse(task_state, api_key, file_bytes, file_type, doc_type, ai_mode):
    try:
        task_state['status'] = 'running'
        mode_label = "Thinking(사고)" if ai_mode == "thinking" else "Flash(고속)"
        task_state['progress_msg'] = f'AI [{mode_label}] 엔진이 {doc_type} 문서를 분석 중입니다...'
        
        prompt = f"""
        Extract document details into JSON format matching the fixed header fields and item list.
        
        CRITICAL RULES FOR EXTRACTION:
        1. HEADER FIELDS EXTRACTION:
           - Extract "to_name", "attn_name", "project_title", "validity", "flag_class", "our_ref", "date_str", "pic", "your_ref", "ship_name", "payment_due".
           - DO NOT mix document title lines or header vessel names into Item #1's Description!

        2. ITEM TABLE EXTRACTION (ACCURATE ITEM & DESCRIPTION SEPARATION):
           - Parse ALL rows inside the line items table.
           - "PartNo": Part number if present (else "").
           - "ItemName": Primary equipment, service name, or title (e.g., "VDR APT", "Radio Survey", "Magnetron").
           - "Description": Specific model, serial no, detailed specs, or sub-lines (e.g., "JRC, JCY-1800", "Busan <--> Yeosu").
           - "Qty": Quantity (Number, default 1).
           - "UnitPrice": Unit price (Number).
           - "Amount": Total amount (Number).
           - "Remarks": Inline remarks/notes.

        3. NO NULL STRINGS:
           - Use "" for missing strings. Do NOT output "nan" or "N/A".

        Extract details into valid JSON EXACTLY matching this structure:
        {{
            "to_name": "",
            "attn_name": "",
            "project_title": "",
            "validity": "",
            "flag_class": "",
            "our_ref": "",
            "date_str": "",
            "pic": "",
            "your_ref": "",
            "ship_name": "",
            "payment_due": "",
            "currency": "KRW",
            "items": [
                {{
                    "PartNo": "", 
                    "ItemName": "", 
                    "Description": "", 
                    "Qty": 1, 
                    "UnitPrice": 0.0, 
                    "Amount": 0.0, 
                    "Remarks": ""
                }}
            ]
        }}
        Return ONLY raw JSON.
        """
        content = Image.open(io.BytesIO(file_bytes)) if file_type in ['png', 'jpg', 'jpeg'] else {"mime_type": "application/pdf", "data": file_bytes}
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
        for idx, s_name in enumerate(sheet_names):
            task_state['progress_msg'] = f"[{idx+1}/{len(sheet_names)}] '{s_name}' 시트 추출 중..."
            try:
                df_clean = pd.read_excel(excel_file, sheet_name=s_name).dropna(how='all').dropna(how='all', axis=1)
                if not df_clean.empty:
                    prompt = f"Extract ALL items from sheet '{s_name}' into JSON Array: [{{\"PartNo\":\"\", \"ItemName\":\"\", \"Description\":\"\", \"UnitPrice\":100.0, \"Remarks\":\"\"}}]."
                    res = get_ai_response(api_key, [prompt, f"CSV:\n{df_clean.to_csv(index=False)}"], mode=ai_mode)
                    if isinstance(res, list): all_results.extend(res)
            except Exception: pass
            
        parsed_df = pd.DataFrame(all_results)
        for col in ['PartNo', 'ItemName', 'Description', 'UnitPrice', 'Remarks']:
            if col not in parsed_df.columns: parsed_df[col] = '' if col != 'UnitPrice' else 0.0
        parsed_df['UnitPrice'] = pd.to_numeric(parsed_df['UnitPrice'], errors='coerce').fillna(0.0)
        task_state['result'] = clean_df(parsed_df)
        task_state['status'] = 'completed'
    except Exception as e:
        task_state['status'] = 'error'
        task_state['error_msg'] = str(e)

def start_bg_thread(target_func, args):
    t = threading.Thread(target=target_func, args=args)
    add_script_run_ctx(t)
    t.start()

# ==========================================
# 5. UI 및 사이드바 (ONE - ERP 로 명칭 변경)
# ==========================================
st.sidebar.title("🚢 ONE - ERP")
st.sidebar.markdown("""<div style="background: rgba(2, 132, 199, 0.1); border: 1px solid #0284C7; border-radius: 8px; padding: 10px 12px; text-align: center; margin-bottom: 20px;"><span style="color: #0284C7; font-size: 0.85rem; font-weight: 800;">✨ Powered by WeasyPrint & Gemini</span></div>""", unsafe_allow_html=True)

menu = st.sidebar.radio("SYSTEM MENU", ["서류 통합 생성", "서류 관리대장", "마스터 DB 관리", "발행 이력 조회"])

task = st.session_state['bg_task']
if is_running:
    st.markdown(f"""<div class="loader-container"><div class="spinner"></div><div class="loader-text">{task['progress_msg']} <br><span style='font-size:0.85rem; color:var(--text-color); opacity:0.75; font-weight:500;'>작업 중에도 다른 메뉴로 자유롭게 이동하실 수 있습니다.</span></div></div>""", unsafe_allow_html=True)
elif task['status'] == 'error': st.error(f"❌ AI 작업 오류: {task['error_msg']}")

# ==========================================
# 6. 서류 통합 생성
# ==========================================
if menu == "서류 통합 생성":
    doc_type = st.sidebar.selectbox("📋 서류 유형 선택", ["Quotation", "Invoice", "Delivery Note", "Purchase Order", "Credit Note", "Service Report"])

    st.markdown(f"""<div class="main-header"><h1>📄 스마트 서류 자동 생성 시스템 ({doc_type})</h1><p>AI 문서 분석을 기반으로 고정 양식 및 마스터 DB 연동 생성을 지원합니다.</p></div>""", unsafe_allow_html=True)

    db = clean_df(pd.read_csv(DB_FILE))

    if task['status'] == 'completed' and task['type'] == 'doc_parse':
        ai_data = task['result']['ai_data']
        st.session_state['doc_info'] = {
            "to": ai_data.get("to_name", ""), "attn": ai_data.get("attn_name", ""), "project_title": ai_data.get("project_title", ""),
            "validity": ai_data.get("validity", ""), "flag_class": ai_data.get("flag_class", ""), "our_ref": ai_data.get("our_ref", ""),
            "date": ai_data.get("date_str", datetime.now().strftime("%Y-%m-%d")), "pic": ai_data.get("pic", ""),
            "your_ref": ai_data.get("your_ref", ""), "ship": ai_data.get("ship_name", ""), "payment_due": ai_data.get("payment_due", ""),
            "currency": ai_data.get("currency", "KRW"), "bottom_remarks": st.session_state['doc_info'].get("bottom_remarks", "")
        }
        
        parsed_items = ai_data.get("items", [])
        items_df = pd.DataFrame(parsed_items) if parsed_items else pd.DataFrame()
        if not items_df.empty:
            items_df["No"] = range(1, len(items_df) + 1)
            for req_col in ["PartNo", "ItemName", "Description", "Qty", "UnitPrice", "Amount", "Remarks"]:
                if req_col not in items_df.columns: items_df[req_col] = "" if req_col not in ["Qty", "UnitPrice", "Amount"] else (1 if req_col == "Qty" else 0.0)

            for idx, row in items_df.iterrows():
                pno, iname, desc = str(row.get('PartNo', '')), str(row.get('ItemName', '')), str(row.get('Description', ''))
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
            st.session_state['doc_items'] = clean_df(items_df)
        st.session_state['bg_task']['status'] = 'idle'
        st.success("✅ AI 분석 완료. 결과가 반영되었습니다.")

    left_col, right_col = st.columns([4, 6])

    with left_col:
        with st.expander("🤖 AI 문서 분석", expanded=False):
            ai_mode_choice = st.radio("AI 분석 엔진 선택", ["⚡ 고속 Flash 모드", "🧠 심층 Thinking (사고) 모드"], horizontal=True, disabled=is_running)
            selected_mode = "thinking" if "Thinking" in ai_mode_choice else "flash"
            uploaded_doc = st.file_uploader("문서 업로드 (PDF, JPG, PNG)", type=["pdf", "png", "jpg", "jpeg"], disabled=is_running)
            if uploaded_doc and st.button("✨ AI 문서 분석", disabled=is_running):
                st.session_state['bg_task']['type'] = 'doc_parse'
                start_bg_thread(run_bg_doc_parse, (st.session_state['bg_task'], gemini_key, uploaded_doc.getvalue(), uploaded_doc.name.split('.')[-1].lower(), doc_type, selected_mode))
                st.rerun()

        if st.button("🔄 서류 입력 초기화", disabled=is_running):
            st.session_state['doc_info'] = {"to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "", "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "", "payment_due": "", "currency": "KRW", "bottom_remarks": ""}
            st.session_state['doc_items'] = pd.DataFrame([{"No": 1, "PartNo": "", "ItemName": "", "Description": "", "Qty": 1, "UnitPrice": 0.0, "Amount": 0.0, "Remarks": ""}])
            st.rerun()

        history = load_history()
        st.markdown('<div class="erp-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="section-title">📌 {doc_type} 헤더 입력</div>', unsafe_allow_html=True)
        
        sel_to = st.selectbox("To (수신처 선택)", options=[""] + history["to_list"])
        to_name = st.text_input("To", value=st.session_state['doc_info']["to"] if not sel_to else sel_to)
        
        sel_attn = st.selectbox("Attention (참조/담당자 선택)", options=[""] + history["attns"])
        attn_name = st.text_input("Attention", value=st.session_state['doc_info']["attn"] if not sel_attn else sel_attn)
        
        project_title = st.text_input("Project Title", value=st.session_state['doc_info'].get("project_title", ""))
        our_ref = st.text_input("Our Ref. No.", value=st.session_state['doc_info'].get("our_ref", ""))
        your_ref = st.text_input("Your Ref. No.", value=st.session_state['doc_info'].get("your_ref", ""))
        date_str = st.text_input("Date", value=st.session_state['doc_info'].get("date", datetime.now().strftime("%Y-%m-%d")))
        validity = st.text_input("Validity", value=st.session_state['doc_info'].get("validity", ""))
        payment_due = st.text_input("Payment Due", value=st.session_state['doc_info'].get("payment_due", ""))
        pic_name = st.text_input("PIC", value=st.session_state['doc_info'].get("pic", ""))
        
        sel_ship = st.selectbox("Ship's Name (선박명 선택)", options=[""] + history["ships"])
        ship_name = st.text_input("Ship's Name", value=st.session_state['doc_info']["ship"] if not sel_ship else sel_ship)

        # Flag / Class 선택
        col_fc1, col_fc2 = st.columns(2)
        with col_fc1: sel_flag = st.selectbox("Flag", FLAG_OPTIONS)
        with col_fc2: sel_class = st.selectbox("Class", CLASS_OPTIONS)
            
        curr_fc = st.session_state['doc_info'].get("flag_class", "")
        if sel_flag != "선택 안함" or sel_class != "선택 안함":
            flag_part = sel_flag if sel_flag != "선택 안함" else ""
            class_part = sel_class if sel_class != "선택 안함" else ""
            auto_fc = f"{flag_part} / {class_part}".strip(" /")
        else: auto_fc = curr_fc

        flag_class = st.text_input("Flag / Class", value=auto_fc)

        curr_val = st.session_state['doc_info'].get("currency", "KRW")
        currency = st.selectbox("Currency (통화)", ["KRW", "USD", "EUR"], index=["KRW", "USD", "EUR"].index(curr_val) if curr_val in ["KRW", "USD", "EUR"] else 0)

        st.markdown('<div class="section-title" style="margin-top:16px;">📦 품목 상세 내역</div>', unsafe_allow_html=True)
        
        item_name_list = [x for x in db["ItemName"].dropna().unique().tolist() if str(x).strip()] if not db.empty and "ItemName" in db.columns else []
        column_config = {
            "PartNo": st.column_config.TextColumn("PartNo", width="small"),
            "ItemName": st.column_config.SelectboxColumn("Item Name", options=item_name_list, width="medium") if item_name_list else st.column_config.TextColumn("Item Name", width="medium"),
            "Description": st.column_config.TextColumn("Description", width="large"),
            "Qty": st.column_config.NumberColumn("Q'ty", format="%,d", min_value=1),
            "UnitPrice": st.column_config.NumberColumn("Unit Price", format="%,d", min_value=0),
            "Amount": st.column_config.NumberColumn("Amount", format="%,d", min_value=0),
            "Remarks": st.column_config.TextColumn("Remarks", width="medium"),
        }

        df_current = clean_df(st.session_state['doc_items'].copy())
        for c in ["PartNo", "ItemName", "Description", "Qty", "UnitPrice", "Amount", "Remarks"]:
            if c not in df_current.columns: df_current[c] = "" if c not in ["Qty", "UnitPrice", "Amount"] else (1 if c == "Qty" else 0.0)

        for i, row in df_current.iterrows():
            if (float(row.get('Amount', 0.0)) == 0.0) and (float(row.get('UnitPrice', 0.0)) > 0):
                df_current.at[i, 'Amount'] = float(row.get('Qty', 1)) * float(row.get('UnitPrice', 0.0))

        edited_df = clean_df(st.data_editor(df_current, column_config=column_config, num_rows="dynamic", use_container_width=True))

        for i, row in edited_df.iterrows():
            if pd.notna(row.get('ItemName')) and row['ItemName'] in db['ItemName'].values:
                match_row = db[db['ItemName'] == row['ItemName']].iloc[0]
                if not row.get('PartNo') or pd.isna(row.get('PartNo')): edited_df.at[i, 'PartNo'] = clean_str(match_row.get('PartNo', ''))
                if not row.get('Description') or pd.isna(row.get('Description')): edited_df.at[i, 'Description'] = clean_str(match_row.get('Description', ''))
                if row.get('UnitPrice', 0.0) == 0.0 or pd.isna(row.get('UnitPrice')):
                    u_p = float(match_row.get('UnitPrice', 0.0))
                    edited_df.at[i, 'UnitPrice'] = u_p
                    if edited_df.at[i, 'Amount'] == 0.0: edited_df.at[i, 'Amount'] = u_p * float(row.get('Qty', 1))

        if "Amount" in edited_df.columns:
            total_val = pd.to_numeric(edited_df["Amount"], errors='coerce').fillna(0).sum()
            st.markdown(f'<div class="total-badge">Total Amount: {currency if currency else "KRW"} {total_val:,.2f}</div>', unsafe_allow_html=True)
        else: total_val = 0.0

        st.markdown('<div class="section-title" style="margin-top:16px;">📝 Remarks & Deviations</div>', unsafe_allow_html=True)
        bottom_remarks = st.text_area("하단 비고란", value=st.session_state['doc_info'].get("bottom_remarks", ""), height=80)
        
        st.markdown('<div class="section-title" style="margin-top:16px;">📌 관리대장 저장</div>', unsafe_allow_html=True)
        if st.button("📥 관리대장 및 마스터 DB 등록", type="secondary", disabled=is_running):
            st.session_state['doc_info'] = {"to": to_name, "attn": attn_name, "project_title": project_title, "validity": validity, "flag_class": flag_class, "our_ref": our_ref, "date": date_str, "pic": pic_name, "your_ref": your_ref, "ship": ship_name, "payment_due": payment_due, "currency": currency, "bottom_remarks": bottom_remarks}
            st.session_state['doc_items'] = clean_df(edited_df)
            db_items = edited_df[['PartNo', 'ItemName', 'Description', 'UnitPrice', 'Remarks']].copy()
            db_items['UnitPrice'] = pd.to_numeric(db_items['UnitPrice'], errors='coerce').fillna(0.0)
            safe_merge_db(db, db_items).to_csv(DB_FILE, index=False)
            save_to_ledger(doc_type, your_ref, our_ref, ship_name, to_name, date_str, currency, total_val, len(edited_df))
            save_history(ship_name, to_name, attn_name)
            st.success("🎉 서류 관리대장 및 마스터 DB 등록 완료")
        st.markdown('</div>', unsafe_allow_html=True)

    with right_col:
        st.markdown('<div class="erp-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">⚡ 실시간 PDF 문서 미리보기</div>', unsafe_allow_html=True)
        
        pdf_formatted_items = prepare_items_for_pdf(clean_df(edited_df).to_dict("records"))
        preview_ctx = {
            "doc_title": doc_type.upper(), "to_name": to_name, "attn_name": attn_name, "project_title": project_title,
            "validity": validity, "flag_class": flag_class, "our_ref": our_ref, "date_str": date_str or datetime.now().strftime("%Y-%m-%d"),
            "pic": pic_name, "your_ref": your_ref, "ship_name": ship_name, "payment_due": payment_due, "currency": currency or "KRW",
            "items": pdf_formatted_items, "bottom_remarks": bottom_remarks
        }
        
        realtime_pdf_bytes = generate_pdf(preview_ctx)
        file_n = f"{doc_type}_{our_ref or your_ref or 'Draft'}.pdf"
        st.download_button("💾 완성된 PDF 다운로드", realtime_pdf_bytes, file_name=file_n, mime="application/pdf", key="rt_download")
        
        pdf_imgs = render_pdf_images(realtime_pdf_bytes)
        if pdf_imgs:
            for i, img_b in enumerate(pdf_imgs):
                st.image(img_b, caption=f"Page {i+1}", use_container_width=True)
        else:
            st.info("PDF 미리보기를 생성 중입니다...")
        st.markdown('</div>', unsafe_allow_html=True)

# ==========================================
# 7. 서류 관리대장 ~ 기타 메뉴
# ==========================================
elif menu == "서류 관리대장":
    ledger_df = pd.read_csv(LEDGER_FILE) if os.path.exists(LEDGER_FILE) else pd.DataFrame()
    st.markdown('<div class="erp-card">', unsafe_allow_html=True)
    if not ledger_df.empty:
        st.dataframe(clean_df(ledger_df), use_container_width=True)
        st.download_button("📥 엑셀(CSV) 다운로드", ledger_df.to_csv(index=False, encoding='utf-8-sig'), file_name="ledger.csv", mime="text/csv")
    else: st.info("데이터가 없습니다.")
    st.markdown('</div>', unsafe_allow_html=True)

elif menu == "마스터 DB 관리":
    db = clean_df(pd.read_csv(DB_FILE))
    
    if task['status'] == 'completed' and task['type'] == 'db_parse':
        st.session_state['temp_db_upload'] = clean_df(task['result'])
        st.session_state['bg_task']['status'] = 'idle'
        st.success("🎉 시트 AI 파싱 완료")

    st.markdown('<div class="erp-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">🤖 AI 단가표 수집기</div>', unsafe_allow_html=True)
    
    ai_mode_choice_db = st.radio("AI 분석 엔진 선택", ["⚡ 고속 Flash 모드", "🧠 심층 Thinking (사고) 모드"], horizontal=True, disabled=is_running, key="db_ai_mode")
    selected_mode_db = "thinking" if "Thinking" in ai_mode_choice_db else "flash"
    uploaded_db_file = st.file_uploader("단가표 파일 업로드", type=["xlsx", "csv"], disabled=is_running)
    
    if uploaded_db_file:
        sheet_names = pd.ExcelFile(uploaded_db_file).sheet_names
        parse_mode = st.radio("파싱 모드", ["📌 특정 시트 선택", "🚀 전체 시트 파싱"], horizontal=True, disabled=is_running)
        if parse_mode == "📌 특정 시트 선택":
            selected_sheet = st.selectbox("시트 선택", sheet_names, disabled=is_running)
            if st.button("✨ 분석", disabled=is_running):
                st.session_state['bg_task']['type'] = 'db_parse'
                start_bg_thread(run_bg_sheet_parse, (st.session_state['bg_task'], gemini_key, uploaded_db_file.getvalue(), [selected_sheet], selected_mode_db))
                st.rerun()
        else:
            if st.button("🚀 전체 파싱", disabled=is_running):
                st.session_state['bg_task']['type'] = 'db_parse'
                start_bg_thread(run_bg_sheet_parse, (st.session_state['bg_task'], gemini_key, uploaded_db_file.getvalue(), sheet_names, selected_mode_db))
                st.rerun()

    if 'temp_db_upload' in st.session_state and not st.session_state['temp_db_upload'].empty:
        st.dataframe(st.session_state['temp_db_upload'], use_container_width=True)
        if st.button("✅ DB 최종 저장", disabled=is_running):
            updated_db = safe_merge_db(db, st.session_state['temp_db_upload'])
            updated_db.to_csv(DB_FILE, index=False)
            del st.session_state['temp_db_upload']
            st.success("저장 완료")
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown('<div class="erp-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📊 DB 관리</div>', unsafe_allow_html=True)
    edited_db = clean_df(st.data_editor(db, num_rows="dynamic", use_container_width=True))
    if st.button("💾 DB 수정사항 저장"):
        edited_db.to_csv(DB_FILE, index=False)
        st.success("저장되었습니다.")
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown('<div class="erp-card">', unsafe_allow_html=True)
    with st.expander("🚨 DB 초기화"):
        pwd_input = st.text_input("비밀번호 입력", type="password", key="reset_pwd")
        if st.button("🔥 초기화") and pwd_input == ADMIN_PASSWORD:
            pd.DataFrame(columns=["PartNo", "ItemName", "Description", "UnitPrice", "Remarks"]).to_csv(DB_FILE, index=False)
            st.success("초기화됨")
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

else:
    files = [f for f in os.listdir("output") if f.endswith('.pdf')]
    if files:
        selected_file = st.selectbox("문서 선택", files)
        if selected_file:
            pdf_data = open(os.path.join("output", selected_file), "rb").read()
            st.download_button("💾 다운로드", pdf_data, file_name=selected_file, mime="application/pdf")
            show_pdf_preview(pdf_data)

if is_running:
    time.sleep(1.0)
    st.rerun()
