import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import io
from datetime import datetime, timedelta
import heapq

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
def topological_sort_tasks(tasks):
    """
    Sorts tasks such that predecessors always appear before successors.
    Secondary sort key is Start Date.
    """
    # 1. Build Graph
    adj = { (t.get('gid') or t['name']): [] for t in tasks }
    in_degree = { (t.get('gid') or t['name']): 0 for t in tasks }
    task_map = { (t.get('gid') or t['name']): t for t in tasks }
    
    for t in tasks:
        u_id = t.get('gid') or t['name']
        deps = t.get('dependencies', [])
        for pred_id in deps:
            # If pred is not in current list (maybe filtered?), skip
            if pred_id not in task_map: continue
            
            adj[pred_id].append(u_id)
            in_degree[u_id] += 1
            
    # 2. Initialize Queue with 0 in-degree tasks
    # Heap element: (start_date_str, task_name, task_id)
    # Using start date for priority
    queue = []
    
    for t in tasks:
        tid = t.get('gid') or t['name']
        if in_degree[tid] == 0:
            start = t.get('start_on', '9999-99-99')
            heapq.heappush(queue, (start, t['name'], tid))
            
    sorted_tasks = []
    while queue:
        start, _, u = heapq.heappop(queue)
        t = task_map[u]
        sorted_tasks.append(t)
        
        for v in adj[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                v_task = task_map[v]
                v_start = v_task.get('start_on', '9999-99-99')
                heapq.heappush(queue, (v_start, v_task['name'], v))
                
    # If cycle exists, some tasks might be left out. 
    # Check length and append remaining if needed (fallback)
    if len(sorted_tasks) < len(tasks):
        seen = set( (t.get('gid') or t['name']) for t in sorted_tasks )
        remaining = [t for t in tasks if (t.get('gid') or t['name']) not in seen]
        # Just append remaining sorted by date
        remaining.sort(key=lambda x: x.get('start_on', ''))
        sorted_tasks.extend(remaining)
        
    return sorted_tasks

def generate_gantt_chart(tasks):
    if not tasks:
        st.warning("No tasks to display.")
        return None

    # Prepare data for Plotly Timeline
    # We want 2 bars per task: Actual and Expected.
    
    gantt_data = []
    
    # Sort tasks TOPOLOGICALLY + DATE
    tasks = topological_sort_tasks(tasks)
    
    # Create a mapping for Y-axis index to draw lines
    task_y_map = {}
    
    for i, t in enumerate(tasks):
        name = t['name']
        gid = t.get('gid') or name
        task_y_map[gid] = name # Use name for Y-axis
        
        # Helper to ensure visible bar
        def get_vis_finish(s_str, e_str):
            if not s_str or not e_str: return e_str
            if s_str == e_str:
                # Add 1 day for visualization of milestones/same-day tasks
                try:
                    dt = datetime.strptime(e_str, "%Y-%m-%d")
                    dt += timedelta(days=1)
                    return dt.strftime("%Y-%m-%d")
                except: return e_str
            return e_str

        # Actual
        act_start = t.get('start_on')
        act_end = t.get('due_on')
        vis_act_end = get_vis_finish(act_start, act_end)
        
        gantt_data.append({
            "Task": name,
            "Start": act_start,
            "VisualFinish": vis_act_end, # Used for drawing
            "Finish": act_end, # Used for tooltip
            "Type": "Actual",
            "GID": gid
        })
        
        # Expected
        exp_start = t.get('expected_start')
        exp_end = t.get('expected_end')
        vis_exp_end = get_vis_finish(exp_start, exp_end)
        
        if exp_start and exp_end:
            gantt_data.append({
                "Task": name,
                "Start": exp_start,
                "VisualFinish": vis_exp_end,
                "Finish": exp_end,
                "Type": "Expected",
                "GID": gid
            })

    if not gantt_data: return None
    
    df = pd.DataFrame(gantt_data)
    
    chart_height = max(600, len(tasks) * 40)
    
    fig = px.timeline(
        df, x_start="Start", x_end="VisualFinish", y="Task", color="Type",
        title="Project Gantt Chart (Planned vs Actual)", 
        height=chart_height,
        color_discrete_map={"Actual": "#2ca02c", "Expected": "#1f77b4"},
        hover_data={"VisualFinish": False, "Finish": True, "Start": True} # Show real Finish, hide Visual
    )
    
    # Ensure tasks are listed top-to-bottom
    fig.update_yaxes(categoryorder='array', categoryarray=[t['name'] for t in reversed(tasks)]) # reversed for top-to-bottom
    
    # Add Dependency Lines
    # We need to draw lines from Pred End to Succ Start.
    # Coordinates:
    # Y: We need the numeric Y index. Plotly uses categorical axes but we can map names to 0, 1, 2...
    # Actually, easiest is to use 'y' as the category name.
    
    # We need to iterate through tasks and finding dependencies
    # But Plotly traces need X and Y arrays.
    
    for t in tasks:
        suc_gid = t.get('gid')
        suc_name = t['name']
        suc_start = t.get('expected_start') # Use Expected or Actual for lines? Usually Actual (Current Plan)
        # Let's use Actual for dependency visualization as that's the "real" constraint
        suc_start = t.get('start_on') 
        
        if not suc_start: continue
        
        deps = t.get('dependencies', [])
        for pred_gid in deps:
            # Find pred task
            # We need the Predecessor's Name (for Y) and End Date (for X)
            # Efficient lookup needed
             pred_task = next((pt for pt in tasks if pt.get('gid') == pred_gid), None)
             if pred_task:
                 pred_name = pred_task['name']
                 pred_end = pred_task.get('due_on')
                 
                 if pred_end:
                     # Add a line trace
                     fig.add_shape(
                         type='line',
                         x0=pred_end, y0=pred_name,
                         x1=suc_start, y1=suc_name,
                         line=dict(color="gray", width=1, dash="dot"),
                         layer="below"
                     )
                     # Add arrow head? 'line' shape doesn't support arrow. 
                     # Use annotation for arrow
                     # fig.add_annotation(...) too heavy for many lines. 
                     # Simple lines are usually enough for Gantt.
                     
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
        ("Create Tasks & Graph (Upload Excel)", "View Project Graph"))

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

    elif action == "View Project Graph":
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

if __name__ == "__main__":
    main()