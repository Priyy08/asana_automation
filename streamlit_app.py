import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import io
from datetime import datetime

# API Base URL
API_URL = "http://127.0.0.1:8000"

# ==========================================
# HELPER FUNCTIONS (API CLIENT)
# ==========================================

def api_parse_excel(file):
    files = {'file': (file.name, file, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
    try:
        response = requests.post(f"{API_URL}/parse-excel", files=files)
        response.raise_for_status()
        return response.json().get('tasks', [])
    except Exception as e:
        st.error(f"Error parsing file: {e}")
        return []

def api_schedule_tasks(tasks):
    payload = {"tasks": tasks}
    try:
        response = requests.post(f"{API_URL}/schedule", json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Error calculating schedule: {e}")
        return []

def api_sync_asana(config, tasks):
    payload = {"config": config, "tasks": tasks}
    try:
        response = requests.post(f"{API_URL}/sync-asana", json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Error syncing to Asana: {e}")
        return None

def api_fetch_tasks(pat, project_gid):
    params = {"pat": pat, "project_gid": project_gid}
    try:
        response = requests.get(f"{API_URL}/visualize", params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Error fetching tasks: {e}")
        return []

def api_update_date(pat, project_gid, task_gid, new_end_date):
    payload = {
        "config": {"pat": pat, "project_gid": project_gid},
        "task_gid": task_gid,
        "new_end_date": new_end_date
    }
    try:
        response = requests.post(f"{API_URL}/update-task-date", json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Error updating task date: {e}")
        return None

def api_toggle_polling(active, pat, project_gid):
    endpoint = "/start-polling" if active else "/stop-polling"
    payload = {"pat": pat, "project_gid": project_gid} if active else {}
    try:
        requests.post(f"{API_URL}{endpoint}", json=payload)
    except Exception as e:
        print(f"Polling Toggle Error: {e}")

def api_get_polling_status():
    try:
        response = requests.get(f"{API_URL}/polling-status")
        if response.status_code == 200:
            return response.json()
    except:
        return None
    return None

# ==========================================
# STREAMLIT UI
# ==========================================
def generate_gantt_chart(tasks):
    if not tasks:
        st.warning("No tasks to display.")
        return None

    df = pd.DataFrame(tasks)
    # Ensure columns exist
    if 'start_on' not in df.columns: 
        # API returns 'start_date' for scheduled, 'start_on' for asana fetched. normalize?
        if 'start_date' in df.columns: df['start_on'] = df['start_date']
    if 'due_on' not in df.columns:
        if 'end_date' in df.columns: df['due_on'] = df['end_date']
        
    df = df.sort_values(by='start_on')
    
    chart_height = max(600, len(df) * 30)
    fig = px.timeline(
        df, x_start="start_on", x_end="due_on", y="name",
        title="Project Gantt Chart", height=chart_height
    )
    fig.update_yaxes(categoryorder='array', categoryarray=df['name'].tolist(), autorange="reversed")
    
    # Arrows logic (simplified for visualization)
    # ... (Keep existing arrow logic if data supports it)
    return fig

def main():
    st.set_page_config(page_title="Asana Gantt Manager", layout="wide")
    st.title("Asana Project Manager (FastAPI Backend)")

    # --- SIDEBAR CONFIG ---
    st.sidebar.header("Configuration")
    pat = st.sidebar.text_input("Asana PAT", type="password")
    project_gid = st.sidebar.text_input("Project GID")
    
    config_ready = pat and project_gid
    
    # Auto-Sync Toggle
    if config_ready:
        # Check current backend status
        current_status = api_get_polling_status()
        is_active = current_status.get('active', False) if current_status else False
        
        # Initialize session state if not set
        if 'polling_active' not in st.session_state:
            st.session_state['polling_active'] = is_active
            
        auto_sync = st.sidebar.checkbox("Enable Auto-Sync (Polling)", value=st.session_state['polling_active'])
        
        # Handle User Interaction
        if auto_sync and not st.session_state['polling_active']:
             api_toggle_polling(True, pat, project_gid)
             st.session_state['polling_active'] = True
             st.experimental_rerun()
             
        elif not auto_sync and st.session_state['polling_active']:
             api_toggle_polling(False, pat, project_gid)
             st.session_state['polling_active'] = False
             st.experimental_rerun()
             
        if st.session_state['polling_active']:
             st.sidebar.success("Polling Active (Background)")


    # --- MODE SELECTION ---
    action = st.radio("Select Action:", 
        ("Create Tasks & Graph (Upload Excel)", "View Graph & Manage Dates"))

    if action == "Create Tasks & Graph (Upload Excel)":
        st.header("Upload Excel & Create")
        uploaded_file = st.file_uploader("Choose an Excel file", type=['xlsx'])
        
        if uploaded_file and st.button("Process & Create"):
            if not config_ready:
                st.error("Please enter Asana Credentials first.")
                return

            with st.spinner("Parsing Excel..."):
                raw_tasks = api_parse_excel(uploaded_file)
                if not raw_tasks: return
                
                st.info(f"Found {len(raw_tasks)} tasks. Scheduling...")
                scheduled_tasks = api_schedule_tasks(raw_tasks)
                
                if scheduled_tasks:
                   st.dataframe(scheduled_tasks)
                   
                   # Auto-Sync
                   st.info("Syncing to Asana...")
                   result = api_sync_asana({"pat": pat, "project_gid": project_gid}, scheduled_tasks)
                   if result:
                       st.success(f"Success! {result}")
                       
                   # Visualize
                   fig = generate_gantt_chart(scheduled_tasks)
                   if fig: st.plotly_chart(fig, use_container_width=True)

    elif action == "View Graph & Manage Dates":
        st.header("Visualize Existing Project")
        if not config_ready:
            st.warning("Enter Credentials.")
            return

        if st.button("Fetch & Visualize"):
            with st.spinner("Fetching tasks from Asana..."):
                tasks = api_fetch_tasks(pat, project_gid)
                st.write(f"Fetched {len(tasks)} tasks.")
                fig = generate_gantt_chart(tasks)
                if fig: st.plotly_chart(fig, use_container_width=True)
                
        st.markdown("---")
        st.subheader("Update Task Date (Cascading)")
        col1, col2 = st.columns(2)
        task_gid_input = col1.text_input("Task GID to Update")
        new_date_input = col2.date_input("New End Date")
        
        if st.button("Update Date"):
            if not task_gid_input:
                st.error("Task GID required")
            else:
                 with st.spinner("Updating & Recalculating Dependencies..."):
                     res = api_update_date(pat, project_gid, task_gid_input, str(new_date_input))
                     if res:
                         st.success(f"Update Complete. {res.get('updated_tasks')} tasks adjusted.")

if __name__ == "__main__":
    main()