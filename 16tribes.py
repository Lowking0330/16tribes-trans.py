import streamlit as st
import os
import sqlite3
import pandas as pd
import time
import base64
import json
import subprocess
from pydub import AudioSegment
from gradio_client import Client, handle_file
import streamlit.components.v1 as components

# --- 1. 16 族語配置表 ---
LANG_MATRIX = {
    "太魯閣語": {"asr": "formosan_trv", "eth": "太魯閣"},
    "阿美語": {"asr": "formosan_ami", "eth": "阿美"},
    "排灣語": {"asr": "formosan_pwn", "eth": "排灣"},
    "布農語": {"asr": "formosan_bun", "eth": "布農"},
    "泰雅語": {"asr": "formosan_tay", "eth": "泰雅"},
    "魯凱語": {"asr": "formosan_dru", "eth": "魯凱"},
    "卑南語": {"asr": "formosan_puy", "eth": "卑南"},
    "鄒語": {"asr": "formosan_tsu", "eth": "鄒"},
    "賽夏語": {"asr": "formosan_sai", "eth": "賽夏"},
    "雅美語(達悟語)": {"asr": "formosan_tao", "eth": "雅美"},
    "邵語": {"asr": "formosan_tha", "eth": "邵"},
    "噶瑪蘭語": {"asr": "formosan_kab", "eth": "噶瑪蘭"},
    "撒奇萊雅語": {"asr": "formosan_sak", "eth": "撒奇萊雅"},
    "賽德克語": {"asr": "formosan_sed", "eth": "賽德克"},
    "拉阿魯哇語": {"asr": "formosan_laa", "eth": "拉阿魯哇"},
    "卡那卡那富語": {"asr": "formosan_kan", "eth": "卡那卡那富"}
}

# --- 2. 系統設定與視覺美化 (v17/v18) ---
st.set_page_config(page_title="族語影音全功能工作站 v18-Final", layout="wide")

# 注入 CSS：包含呼吸燈輸入框、10:2 物理比例、80px 限高與專業主題
st.markdown("""
    <style>
    .stApp { background-color: #0e1117; font-family: 'Microsoft JhengHei', sans-serif; }
    
    /* 族語輸入框：呼吸高亮與 80px 物理限高 */
    div[data-testid="stTextArea"] textarea {
        border-radius: 12px !important;
        border: 2px solid #4b5563 !important;
        background-color: #1f2937 !important;
        color: #ffffff !important;
        transition: all 0.3s ease-in-out !important;
    }
    div[data-testid="stTextArea"] textarea:focus {
        border-color: #3b82f6 !important;
        box-shadow: 0 0 12px rgba(59, 130, 246, 0.6) !important;
        background-color: #111827 !important;
    }
    div[data-testid="stTextArea"] > div { max-height: 80px !important; }

    /* 頂部 Tab 美化 */
    .stTabs [data-baseweb="tab-list"] { gap: 15px; }
    .stTabs [data-baseweb="tab"] {
        background-color: #374151 !important;
        border-radius: 8px 8px 0 0 !important;
        color: #9ca3af !important;
        padding: 8px 25px !important;
    }
    .stTabs [aria-selected="true"] { background-color: #2563eb !important; color: white !important; }
    
    /* 按鈕圓角 */
    button { border-radius: 10px !important; }
    </style>
    """, unsafe_allow_html=True)

# 右上角自動備份標籤顯示
save_time = time.strftime("%H:%M:%S", time.localtime())
st.markdown(f"""
    <div style="position: fixed; top: 0.8rem; right: 1rem; z-index: 9999; pointer-events: none;">
        <span style="background-color: #1b5e20; color: #ccff90; padding: 6px 15px; border-radius: 30px; 
                     font-size: 0.85rem; font-weight: bold; border: 1px solid #2e7d32;
                     box-shadow: 0 4px 15px rgba(0,0,0,0.5);">
            🛡️ 系統安全防護中 | 自動備份於 {save_time}
        </span>
    </div>
""", unsafe_allow_html=True)

# 資料夾與資料庫初始化
V_FOLDER = os.path.join(os.getcwd(), "saved_videos")
if not os.path.exists(V_FOLDER): os.makedirs(V_FOLDER)

if 'output_ready' not in st.session_state: st.session_state['output_ready'] = False
if 'final_srt_data' not in st.session_state: st.session_state['final_srt_data'] = None
if 'final_video_path' not in st.session_state: st.session_state['final_video_path'] = None
if 'srt_res' not in st.session_state: st.session_state['srt_res'] = None
if 'video_res' not in st.session_state: st.session_state['video_res'] = None
if 'play_trigger_s' not in st.session_state: st.session_state['play_trigger_s'] = 0.0
if 'temp_edits' not in st.session_state: st.session_state['temp_edits'] = {}
if 'last_autosave_time' not in st.session_state: st.session_state['last_autosave_time'] = time.time()

def get_db_connection():
    db_path = os.path.join(os.getcwd(), 'truku_pro_assets.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS corpus 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  lang TEXT, raw_text TEXT, translated_text TEXT, 
                  start_ms INTEGER, video_path TEXT, created_at DATETIME)''')
    conn.commit(); conn.close()

init_db()

@st.cache_resource
def init_clients():
    try:
        asr = Client("https://sapolita-kaldi.ithuan.tw/")
        mt = Client("ithuan/formosan-translation")
        return asr, mt
    except Exception as e:
        st.error(f"❌ API 連線失敗: {e}"); return None, None

ASR_CLIENT, MT_CLIENT = init_clients()

# --- 3. 核心引擎與處理邏輯 ---
def robust_predict(client, api_name, *args):
    for attempt in range(3):
        try: return client.predict(*args, api_name=api_name)
        except:
            if attempt < 2: time.sleep(2); continue
            raise

def get_srt_time(ms):
    td = time.gmtime(ms / 1000.0)
    return f"{time.strftime('%H:%M:%S', td)},{int(ms % 1000):03}"

def burn_subtitles_v16(video_input, srt_path, output_path):
    abs_srt_path = os.path.abspath(srt_path).replace("\\", "/").replace(":", "\\:")
    style = "FontSize=18,Bold=0,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,BorderStyle=1,Outline=1,Shadow=1,Alignment=2,MarginV=15"
    cmd = ['ffmpeg', '-y', '-i', video_input, '-vf', f"subtitles='{abs_srt_path}':force_style='{style}'", '-c:a', 'copy', output_path]
    return subprocess.run(cmd, capture_output=True).returncode == 0

def custom_player_v16_sync(video_path, jump_s, all_subs_json, loop_mode):
    with open(video_path, "rb") as f: v_base64 = base64.b64encode(f.read()).decode()
    loop_js = "true" if loop_mode else "false"
    js_template = """
    <div style="position: relative; width: 100%; background: #000; border-radius: 12px; overflow: hidden; border: 1px solid #444;">
        <video id="myVideo" width="100%" controls autoplay><source src="data:video/mp4;base64,VAR_V_BASE64" type="video/mp4"></video>
        <div id="sub-overlay" style="position: absolute; bottom: 8%; width: 100%; text-align: center; color: white; font-size: 20px; text-shadow: 2px 2px 3px #000, -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000; pointer-events: none; padding: 0 20px; box-sizing: border-box; line-height: 1.3;"></div>
    </div>
    <script>
    var video = document.getElementById("myVideo"); var overlay = document.getElementById("sub-overlay");
    var subs = VAR_SUBS_JSON; var jumpTarget = VAR_JUMP_S;
    video.currentTime = jumpTarget;
    video.ontimeupdate = function() {
        var now = video.currentTime * 1000; var found = "";
        for (var i = 0; i < subs.length; i++) {
            if (now >= subs[i].start && now <= (subs[i].start + 4500)) {
                found = subs[i].r + "<br><span style='font-size: 0.85em; opacity: 0.9;'>" + subs[i].t + "</span>";
                break;
            }
        }
        overlay.innerHTML = found;
        if (VAR_LOOP_JS && video.currentTime >= (jumpTarget + 4.5)) { video.currentTime = jumpTarget; video.play(); }
    };
    </script>
    """
    return components.html(js_template.replace("VAR_V_BASE64", v_base64).replace("VAR_SUBS_JSON", all_subs_json).replace("VAR_JUMP_S", str(jump_s)).replace("VAR_LOOP_JS", loop_js), height=480)

def perform_autosave():
    if not st.session_state['temp_edits']: return
    conn = get_db_connection()
    try:
        for key, value in st.session_state['temp_edits'].items():
            if '_' not in key: continue
            prefix, rid = key.split('_'); col = "raw_text" if prefix == 'r' else "translated_text"
            conn.execute(f"UPDATE corpus SET {col}=? WHERE id=?", (value, rid))
        conn.commit(); st.session_state['last_autosave_time'] = time.time(); st.toast("🛡️ 已備份進度", icon="⚡")
    finally: conn.close()

def run_v16_workflow(uploaded_file, lang_name):
    config = LANG_MATRIX[lang_name]
    perm_v_path = os.path.abspath(os.path.join(V_FOLDER, f"{int(time.time())}_{uploaded_file.name}"))
    with open(perm_v_path, "wb") as f: f.write(uploaded_file.getvalue())
    
    sound = AudioSegment.from_file(perm_v_path)
    duration_ms, interval_ms = len(sound), 4500
    src_code = str(robust_predict(MT_CLIENT, "/lambda", config['eth'])['value'])
    idx, current_start, srt_content = 1, 0, []
    p_bar = st.progress(0)
    
    while current_start < duration_ms:
        current_end = min(current_start + interval_ms, duration_ms)
        c_file = os.path.abspath(f"v16_tmp_{idx}.wav")
        sound[current_start:current_end].export(c_file, format="wav")
        try:
            raw = robust_predict(ASR_CLIENT, "/automatic_speech_recognition", config['asr'], handle_file(c_file))
            if raw and len(str(raw).strip()) > 1:
                trans = robust_predict(MT_CLIENT, "/translate", str(raw), src_code, "zho_Hant")
                conn = get_db_connection()
                conn.execute("INSERT INTO corpus (lang, raw_text, translated_text, start_ms, video_path, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                             (lang_name, str(raw), str(trans), current_start, perm_v_path, time.strftime('%Y-%m-%d %H:%M:%S')))
                conn.commit(); conn.close()
                srt_content.append(f"{idx}\n{get_srt_time(current_start)} --> {get_srt_time(current_end-100)}\n{raw}\n{trans}\n")
                idx += 1
        finally:
            if os.path.exists(c_file): os.remove(c_file)
        current_start = current_end; p_bar.progress(current_start / duration_ms)
    
    f_srt = "\n".join(srt_content)
    with open("temp.srt", "w", encoding="utf-8") as f: f.write(f_srt)
    out_vid = perm_v_path.replace(".mp4", "_raw.mp4")
    burn_subtitles_v16(perm_v_path, "temp.srt", out_vid)
    return f_srt, out_vid

# --- 4. 介面與分頁邏輯 (v18 效能關鍵) ---
tab1, tab2, tab3 = st.tabs(["🚀 快速處理下載", "✍️ 專業同步校對", "⚙️ 資產管理維護"])

with tab1:
    st.header("全族語快速生成模式")
    sel_lang = st.selectbox("請選擇目標族語：", list(LANG_MATRIX.keys()))
    file = st.file_uploader("上傳影音檔案：", type=['mp4', 'wav', 'mp3', 'm4a'])
    if st.button("🚀 開始處理"):
        if file:
            with st.spinner(f"正在分析【{sel_lang}】..."):
                st.session_state['srt_res'], st.session_state['video_res'] = run_v16_workflow(file, sel_lang)
            st.success("辨識完成！")
    if st.session_state['srt_res']:
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("📥 下載 AI 版 SRT", st.session_state['srt_res'], file_name="ai_raw.srt")
            if os.path.exists(st.session_state['video_res']):
                with open(st.session_state['video_res'], "rb") as f:
                    st.download_button("📥 下載 AI 版燒錄影片", f, file_name="ai_video.mp4")

with tab2:
    st.header("專業同步校對工作站")
    conn = get_db_connection()
    latest = conn.execute("SELECT video_path, lang FROM corpus ORDER BY created_at DESC LIMIT 1").fetchone()
    
    if latest:
        df = pd.read_sql_query("SELECT * FROM corpus WHERE video_path = ? ORDER BY start_ms ASC", conn, params=(latest['video_path'],))
        conn.close()

        if not df.empty:
            st.info(f"🎬 當前校對族語：【{latest['lang']}】")
            if time.time() - st.session_state['last_autosave_time'] > 60: perform_autosave()

            if st.button("🔄 同步更新並產出最終校對版"):
                perform_autosave()
                with st.spinner("正在為您重新燒錄高品質影片..."):
                    final_srt_list = []
                    for i, r in df.iterrows():
                        r_txt = st.session_state['temp_edits'].get(f"r_{r['id']}", r['raw_text'])
                        t_txt = st.session_state['temp_edits'].get(f"t_{r['id']}", r['translated_text'])
                        final_srt_list.append(f"{i+1}\n{get_srt_time(r['start_ms'])} --> {get_srt_time(r['start_ms']+4400)}\n{r_txt}\n{t_txt}\n")
                    st.session_state['final_srt_data'] = "\n".join(final_srt_list)
                    st.session_state['final_video_path'] = latest['video_path'].replace(".mp4", "_final.mp4")
                    with open("final.srt", "w", encoding="utf-8") as fs: fs.write(st.session_state['final_srt_data'])
                    burn_subtitles_v16(latest['video_path'], "final.srt", st.session_state['final_video_path'])
                    st.session_state['output_ready'] = True
                    st.success("✨ 校對版成品已生成！")

            if st.session_state['output_ready']:
                cx, cy = st.columns(2)
                cx.download_button("📥 下載校對版 SRT", st.session_state['final_srt_data'], file_name="corrected.srt")
                if os.path.exists(st.session_state['final_video_path']):
                    with open(st.session_state['final_video_path'], "rb") as fv:
                        cy.download_button("📥 下載校對版影片", fv, file_name="final_corrected.mp4")

            st.divider()
            
            # --- [v18 新增] 分頁控制邏輯 ---
            items_per_page = 20
            total_rows = len(df)
            total_pages = (total_rows // items_per_page) + (1 if total_rows % items_per_page > 0 else 0)
            
            col_ed, col_vi = st.columns([1, 1])
            with col_ed:
                # 分頁器 UI
                p_c1, p_c2 = st.columns([1, 2])
                with p_c1:
                    page_num = st.number_input("目前頁數", min_value=1, max_value=total_pages, value=1)
                with p_c2:
                    st.write(f"第 {page_num} 頁 / 共 {total_pages} 頁 (每頁 {items_per_page} 句)")
                
                is_loop = st.toggle("🔄 循環播放片段", value=False)
                
                # 計算當前頁面範圍
                start_idx = (page_num - 1) * items_per_page
                end_idx = min(start_idx + items_per_page, total_rows)
                current_page_df = df.iloc[start_idx:end_idx]
                
                with st.container(height=550):
                    subs_list = []
                    # 準備給播放器的所有字幕資料
                    for _, row in df.iterrows():
                        cur_r = st.session_state['temp_edits'].get(f"r_{row['id']}", row['raw_text'])
                        cur_t = st.session_state['temp_edits'].get(f"t_{row['id']}", row['translated_text'])
                        subs_list.append({"start": row['start_ms'], "r": cur_r, "t": cur_t})

                    # 僅渲染目前分頁的 UI
                    for i, row in current_page_df.iterrows():
                        cur_r = st.session_state['temp_edits'].get(f"r_{row['id']}", row['raw_text'])
                        cur_t = st.session_state['temp_edits'].get(f"t_{row['id']}", row['translated_text'])
                        
                        with st.expander(f"句序 {i+1} | {cur_r[:15]}..."):
                            if st.button("🎧 播放", key=f"p_{row['id']}"):
                                perform_autosave(); st.session_state['play_trigger_s'] = row['start_ms'] / 1000.0; st.rerun()
                            
                            c_t, c_e = st.columns([10, 2])
                            with c_t:
                                # v17 呼吸燈效果已透過 CSS 注入
                                st.session_state['temp_edits'][f"r_{row['id']}"] = st.text_area("族語", cur_r, key=f"r_{row['id']}", height=80)
                                st.session_state['temp_edits'][f"t_{row['id']}"] = st.text_area("中文", cur_t, key=f"t_{row['id']}", height=80)
                            
                            if st.button("💾 儲存此句", key=f"s_{row['id']}"):
                                c = get_db_connection()
                                c.execute("UPDATE corpus SET raw_text=?, translated_text=? WHERE id=?", 
                                         (st.session_state['temp_edits'][f"r_{row['id']}"], st.session_state['temp_edits'][f"t_{row['id']}"], row['id']))
                                c.commit(); c.close(); st.toast("✅ 已儲存")
            
            with col_vi:
                if not df.empty: custom_player_v16_sync(latest['video_path'], st.session_state['play_trigger_s'], json.dumps(subs_list), is_loop)
    else:
        conn.close()
        st.warning("目前尚無校對資料。")

with tab3:
    st.header("⚙️ 資產管理維護")
    if st.button("⚠️ 清空所有歷史資料"):
        conn = get_db_connection(); conn.execute("DELETE FROM corpus"); conn.commit(); conn.close()
        st.session_state['output_ready'] = False; st.success("已清空紀錄！"); st.rerun()
