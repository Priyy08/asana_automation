from fastapi import FastAPI, UploadFile, File, HTTPException
from typing import List
import pandas as pd
import io
from .models import ScheduleRequest, ScheduledTask, SyncRequest, DateUpdateRequest, AsanaConfig
from .services import Scheduler, AsanaManager
from .date_logic import recalculate_dates, auto_recalibrate
from .database import init_db, save_baseline, update_actuals, get_all_history
import asyncio
import json
import os
import time
import openpyxl
from fastapi.concurrency import run_in_threadpool

CONFIG_FILE = "polling_config.json"

app = FastAPI()

# Global State for Polling
polling_config = {
    "active": False,
    "pat": "",
    "project_gid": "",
    "interval": 20 # Seconds
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                polling_config.update(data)
                print(f"Loaded config: Polling Active={polling_config['active']}")
        except Exception as e:
            print(f"Error loading config: {e}")

def save_config():
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(polling_config, f)
    except Exception as e:
        print(f"Error saving config: {e}")

async def background_poller():
    print("Background Poller Started")
    while True:
        if polling_config["active"] and polling_config["pat"] and polling_config["project_gid"]:
            try:
                # Run sync Asana calls in threadpool to avoid blocking main loop
                manager = AsanaManager(polling_config["pat"], polling_config["project_gid"])
                
                # 1. Fetch
                tasks = await run_in_threadpool(manager.fetch_project_tasks)
                
                # 2. Check for violations & Recalibrate
                # auto_recalibrate returns ONLY modified tasks
                updates = auto_recalibrate(tasks)
                
                # Update DB with current state (Actuals)
                # Pass ALL tasks to update_actuals to ensure we capture everything, 
                # or just do it periodically? Doing it every poll is fine for <100 tasks.
                try:
                    await run_in_threadpool(update_actuals, tasks)
                except Exception as db_e:
                    print(f"DB Update Failed: {db_e}")

                # 3. Push Updates
                if updates:
                    print(f"[Auto-Sync] Violation Detected! Updating {len(updates)} tasks...")
                    for t in updates:
                        await run_in_threadpool(manager.update_task_dates, t['gid'], t['start_on'], t['due_on'])
                    print("[Auto-Sync] Update Complete.")
                else:
                    # print("[Auto-Sync] No violations found.")
                    pass
                    
            except Exception as e:
                print(f"[Auto-Sync] Error: {e}")
        
        await asyncio.sleep(polling_config["interval"])

@app.on_event("startup")
async def startup_event():
    init_db()
    load_config()
    # Force auto-enable if credentials exist
    if polling_config["pat"] and polling_config["project_gid"]:
        polling_config["active"] = True
        print(f"[Startup] Credentials found. Auto-starting polling for project {polling_config['project_gid']}")
    
    asyncio.create_task(background_poller())

@app.post("/start-polling")
async def start_polling(config: AsanaConfig):
    polling_config["pat"] = config.pat
    polling_config["project_gid"] = config.project_gid
    polling_config["active"] = True
    save_config()
    print(f"Polling ENABLED for Project {config.project_gid}")
    return {"status": "Polling Started"}

@app.post("/stop-polling")
async def stop_polling():
    polling_config["active"] = False
    save_config()
    print("Polling DISABLED")
    return {"status": "Polling Stopped"}

@app.get("/polling-status")
async def get_polling_status():
    return polling_config


@app.post("/parse-excel")
async def parse_excel(file: UploadFile = File(...)):
    if not file.filename.endswith('.xlsx'):
        raise HTTPException(status_code=400, detail="Invalid file format")
    
    contents = await file.read()
    try:
        # Use openpyxl to detect styles (Bold = Section)
        wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
        sheet = wb.active
        
        tasks_data = []
        current_section = "General"
        
        col_map = {} # 'Task': idx, 'Triggering': idx, 'days': idx
        header_found = False
        
        for i, row in enumerate(sheet.iter_rows(min_row=1, values_only=False), start=1):
            # Convert row cells to values for logic
            values = [str(c.value).strip() if c.value is not None else "" for c in row]
            
            # 0. Check for Section in Column A (Index 0) ALWAYS
            # Assumption: Section headers are in the first column and are BOLD.
            cell_A = row[0]
            val_A = str(cell_A.value).strip() if cell_A.value else ""
            
            # If Col A is Bold and has text, it's a Section
            if val_A and cell_A.font and cell_A.font.b:
                 if val_A.lower() != "responsible":
                     current_section = val_A
                     # print(f"Section Detected: {current_section}")

            # 1. Detect Header Row
            if not header_found:
                # Debug: Print headers found in potential row
                # print(f"Checking row for headers: {values}")
                if 'Task' in values and 'Triggering task' in values:
                    for idx, val in enumerate(values):
                        if val == 'Task': col_map['Task'] = idx
                        if val == 'Triggering task': col_map['Triggering'] = idx
                        if val == 'days': col_map['days'] = idx
                        if val.lower() == 'team': col_map['Team'] = idx
                        if val.lower() == 'responsible': col_map['Responsible'] = idx
                    header_found = True
                    print(f"Headers Found! Map: {col_map}")
                continue 

            # 2. Logic for Data Rows
            if not col_map: continue
            
            # Process Task
            def get_cell(key):
                idx = col_map.get(key)
                if idx is None or idx >= len(row): return None
                return row[idx]

            task_cell = get_cell('Task')
            if not task_cell or not task_cell.value: continue
            
            task_name = str(task_cell.value).strip()
            if not task_name or task_name.lower() == 'nan': continue
            if task_name in ['Task', 'Triggering task']: continue
            
            # It's a Task
            trig_cell = get_cell('Triggering')
            days_cell = get_cell('days')
            team_cell = get_cell('Team')
            resp_cell = get_cell('Responsible')
            
            triggers_raw = str(trig_cell.value).strip() if trig_cell and trig_cell.value else ""
            days_raw = str(days_cell.value).strip() if days_cell and days_cell.value else ""
            team_val = str(team_cell.value).strip() if team_cell and team_cell.value else ""
            resp_val = str(resp_cell.value).strip() if resp_cell and resp_cell.value else ""
            
            if triggers_raw or days_raw:
                 # print(f"Task: {task_name} | Triggers: '{triggers_raw}' | Days: '{days_raw}'")
                 pass
            
            triggers = [t.strip() for t in triggers_raw.split('|') if t.strip()]
            lags = []
            if days_raw:
                try:
                   lags = [int(float(d.strip())) for d in days_raw.split('|') if d.strip()]
                except: pass
                
            # Use Row Index as Unique ID
            unique_id = f"row_{i}"
            
            tasks_data.append({
                "id": unique_id,
                "name": task_name,
                "duration": 0, 
                "triggering_tasks": triggers,
                "lag_days": lags,
                "section": current_section,
                "team": team_val,
                "responsible": resp_val
            })
            
        return {"tasks": tasks_data}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error parsing Excel: {str(e)}")

@app.post("/schedule")
def schedule_tasks(request: ScheduleRequest):
    scheduler = Scheduler()
    
    # 1. Add all tasks (First Pass)
    for t in request.tasks:
        scheduler.add_task(t.id, t.name, section=t.section, team=t.team, responsible=t.responsible)
        
    # 2. Add Dependencies (Second Pass)
    for t in request.tasks:
        # scheduler.tasks is accessed by ID now
        if t.id in scheduler.tasks:
            # Only apply duration from Row Model if it's explicitly set (non-zero)
            # This prevents overwriting a duration that was set by a Predecessor (via 'Days' column)
            if t.duration > 0:
                scheduler.tasks[t.id]['duration'] = t.duration
        
        if t.id in scheduler.tasks:
            # Row Task Duration defaults to 0 (or 1?)
            # User implies Row Task blocks others. Usually blocking task takes time.
            # But the 'days' column is aligned with the 'Triggering Task' list.
            # So 'days' applies to the *Dependent Task*.
            pass

        for i, trig_name in enumerate(t.triggering_tasks):
            # "Days" column value matches index of Triggering Task
            successor_duration = 1
            if i < len(t.lag_days):
                successor_duration = t.lag_days[i]
            
            # Resolve Dependency: Row Task (t.id) IS PREDECESSOR of Trigger Name (Successor)
            succ_id = scheduler.resolve_successor(predecessor_id=t.id, successor_name=trig_name, lag_days=0)
            
            # Set Duration of the Successor (The Dependent/Triggering Task)
            if succ_id and succ_id in scheduler.tasks:
                scheduler.tasks[succ_id]['duration'] = successor_duration

    # Propagate sections to orphans
    scheduler.inherit_missing_sections()

    scheduler.calculate_dates()
    return scheduler.get_scheduled_tasks()

@app.post("/sync-asana")
def sync_asana(request: SyncRequest):
    manager = AsanaManager(request.config.pat, request.config.project_gid)
    
    # 1. Setup Custom Fields & Fetch Users
    print("Setting up Custom Fields & Fetching Users...")
    ws_gid = manager.get_workspace_gid()
    team_gid = manager.ensure_text_custom_field("Team", ws_gid)
    resp_gid = manager.ensure_text_custom_field("Responsible", ws_gid)
    
    # Fetch Users for Assignment
    users_map = manager.fetch_workspace_users(ws_gid)
    print(f"Fetched {len(users_map)} users for assignment mapping.")
    
    # 2. Create Tasks (Baseline)
    print("Creating Tasks...")
    baseline_tasks = []
    
    created_count = 0
    
    for task in request.tasks:
        # Construct Description (Keep checks just in case)
        notes_parts = []
        if task.team: notes_parts.append(f"Team: {task.team}")
        if task.responsible: notes_parts.append(f"Responsible: {task.responsible}")
        notes_str = "\n".join(notes_parts)

        # Prepare Custom Fields
        c_fields = {}
        if team_gid and task.team: c_fields[team_gid] = task.team
        if resp_gid and task.responsible: c_fields[resp_gid] = task.responsible

        # Resolve Assignee
        assignee_gid = None
        # Try matching 'Responsible' first, then 'Team'
        # Check against name or email in users_map
        if task.responsible:
             assignee_gid = users_map.get(task.responsible.lower())
        
        if not assignee_gid and task.team:
             assignee_gid = users_map.get(task.team.lower())

        # Create task in Asana (Asana allows duplicate names)
        gid = manager.create_task_with_dates(
            task.name, 
            task.start_date, 
            task.end_date, 
            notes=notes_str,
            custom_fields=c_fields,
            assignee=assignee_gid
        )
        if gid:
            # Map Scheduler ID to Asana GID
            manager.task_registry[task.id] = gid
            created_count += 1
            
            baseline_tasks.append({
                'gid': gid,
                'name': task.name,
                'start_on': task.start_date,
                'due_on': task.end_date
            })
    
    try:
        run_in_threadpool(save_baseline, baseline_tasks)
    except Exception as e:
        print(f"Failed to save baseline: {e}")

    # 2. Link Dependencies
    linked_count = 0
    for t in request.tasks:
        # t.id -> Successor GID
        suc_gid = manager.task_registry.get(t.id)
        if not suc_gid: continue
        
        # t.dependencies is list of Predecessor IDs
        for pred_id in t.dependencies:
            pred_gid = manager.task_registry.get(pred_id)
            if pred_gid:
                manager.link_dependency(suc_gid, pred_gid)
                linked_count += 1
                time.sleep(0.3) # Prevent Rate Limiting
                
    # 3. Handle Sections
    print("Handling Sections...")
    gid_map = manager.task_registry # ID -> GID
    
    try:
        for task in request.tasks:
                if task.section and task.id in gid_map:
                    try:
                        sec_gid = manager.get_or_create_section(task.section)
                        if sec_gid:
                            manager.move_task_to_section(gid_map[task.id], sec_gid)
                    except Exception as e:
                        print(f"Failed to move {task.name} to section {task.section}: {e}")
    except Exception as ie:
        print(f"CRITICAL ERROR in Section Loop: {ie}")
        import traceback
        traceback.print_exc()

    return {"status": "success", "created": created_count, "linked": linked_count}

@app.post("/update-task-date")
async def update_task_date(request: DateUpdateRequest):
    manager = AsanaManager(request.config.pat, request.config.project_gid)
    
    try:
        # 1. Fetch current state
        tasks = manager.fetch_project_tasks()
        
        # 2. Recalculate
        modified_tasks = recalculate_dates(tasks, request.task_gid, request.new_end_date)
        
        # 3. Push updates
        updated_count = 0
        for t in modified_tasks:
            # Skip the one we triggered? Or update it too (User might have updated it in UI already? 
            # If we call this endpoint, we assume we want to enforce the change)
            manager.update_task_dates(t['gid'], t['start_on'], t['due_on'])
            updated_count += 1
            
        return {"status": "success", "updated_tasks": updated_count}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/visualize")
async def visualize(pat: str, project_gid: str):
    manager = AsanaManager(pat, project_gid)
    try:
        tasks = manager.fetch_project_tasks()
        
        # Enrich with Expected Dates from DB
        history = get_all_history()
        
        for t in tasks:
            gid = t['gid']
            if gid in history:
                t['expected_start'] = history[gid]['expected_start']
                t['expected_end'] = history[gid]['expected_end']
            else:
                 # Fallback if not in DB (new task?)
                 t['expected_start'] = t['start_on']
                 t['expected_end'] = t['due_on']
                 
        return tasks
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
