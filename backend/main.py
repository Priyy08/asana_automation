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
        
        for row in sheet.iter_rows():
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
                if 'Task' in values and 'Triggering task' in values:
                    for idx, val in enumerate(values):
                        if val == 'Task': col_map['Task'] = idx
                        if val == 'Triggering task': col_map['Triggering'] = idx
                        if val == 'days': col_map['days'] = idx
                    header_found = True
                    # The detections are 0-indexed relative to row values.
                    # row[0] is Column A.
                continue 

            # 2. Logic for Data Rows
            if not col_map: continue
            
            # (Section check moved to top)
            
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
            
            triggers_raw = str(trig_cell.value).strip() if trig_cell and trig_cell.value else ""
            days_raw = str(days_cell.value).strip() if days_cell and days_cell.value else ""
            
            triggers = [t.strip() for t in triggers_raw.split('|') if t.strip()]
            lags = []
            if days_raw:
                try:
                   lags = [int(float(d.strip())) for d in days_raw.split('|') if d.strip()]
                except: pass
                
            tasks_data.append({
                "name": task_name,
                "duration": 0, 
                "triggering_tasks": triggers,
                "lag_days": lags,
                "section": current_section
            })
            
        return {"tasks": tasks_data}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error parsing Excel: {str(e)}")

@app.post("/schedule", response_model=List[ScheduledTask])
async def schedule_tasks(request: ScheduleRequest):
    scheduler = Scheduler()
    
    # Load tasks
    for t_model in request.tasks:
        scheduler.add_task(t_model.name, section=t_model.section)
        # Note: Original code set duration based on 'days' column of the *Triggering Task* row context? 
        # Actually checking original code:
        # for i, suc_name in enumerate(triggers):
            # duration = lags[i]
            # scheduler.add_task(suc_name) ... duration ...
        # It seems the 'days' column in the Excel was associated with the triggering tasks list?
        # Let's stick to a simplified interpretation or the one that matches the original exactly if possible.
        # Original:
        # task_name = row['Task'] (This is the one being defined)
        # triggers = row['Triggering task']
        # days = row['days']
        # "scheduler.add_task(suc_name)" -> wait, 'suc_name' was from triggers loop?
        # "scheduler.add_dependency(task_name, suc_name, 0)" -> task_name depends on suc_name?
        # No: add_dependency(predecessor, successor, lag).
        # Original: add_dependency(task_name, suc_name, 0).
        # This means task_name (Row Task) IS THE PREDECESSOR of suc_name (Triggering Task??)
        # "Triggering task" name suggests it triggers the current task...
        # But `add_dependency(task_name, suc_name)` means task_name -> suc_name.
        # If 'Triggering task' means "Tasks that this task triggers", then yes.
        # But usually 'Triggering task' column implies "Prerequisites".
        # Let's look at: 
        # "scheduler.add_task(suc_name)" inside the trigger loop.
        # "scheduler.tasks[suc_name]['duration'] = duration"
        # So the duration applies to the tasks in the 'Triggering task' column?
        # This is a bit odd naming in the excel, but we must follow the logic.
        
        # Current Logic in endpoint:
        # We receive a list of tasks with their own properties.
        # We need to construct the graph.
        
        pass

    # Re-evaluating the Excel Parsing to match generic logic:
    # Let's assume the /schedule endpoint receives a clean list of "Task items"
    # And we build the graph.
    
    for t in request.tasks:
        scheduler.add_task(t.name)
        # If the input model has duration, set it.
        scheduler.tasks[t.name]['duration'] = t.duration
        
    # Add dependencies
    for t in request.tasks:
        # t.name is the "Row Task"
        # t.triggering_tasks are the ones listed in "Triggering task" column.
        # In original code: 
        # for suc_name in triggers:
        #    scheduler.add_dependency(task_name, suc_name)
        # So Row Task -> Triggering Task (Row Task is Predecessor)
        
        for i, trig in enumerate(t.triggering_tasks):
            scheduler.add_task(trig) # Ensure it exists
            # Duration logic from original:
            if i < len(t.lag_days):
                scheduler.tasks[trig]['duration'] = t.lag_days[i]
            
            scheduler.add_dependency(t.name, trig, 0)

    # Propagate sections to orphans
    scheduler.inherit_missing_sections()

    scheduler.calculate_dates()
    return scheduler.get_scheduled_tasks()

@app.post("/sync-asana")
async def sync_asana(request: SyncRequest):
    manager = AsanaManager(request.config.pat, request.config.project_gid)
    
    # 1. Create Tasks
    created_count = 0
    for t in request.tasks:
        gid = manager.create_task_with_dates(t.name, t.start_date, t.end_date)
        if gid:
            manager.task_registry[t.name] = gid
            created_count += 1
        time.sleep(0.2) # Prevent Rate Limiting
    
    # Save Baseline to DB
    # We need to construct the list of tasks with GIDs and Dates
    # The 'request.tasks' has names and initial dates. 'manager.task_registry' has GIDs.
    
    baseline_tasks = []
    for t in request.tasks:
        gid = manager.task_registry.get(t.name)
        if gid:
            baseline_tasks.append({
                'gid': gid,
                'name': t.name,
                'start_on': t.start_date,
                'due_on': t.end_date
            })
    
    try:
        await run_in_threadpool(save_baseline, baseline_tasks)
    except Exception as e:
        print(f"Failed to save baseline: {e}")

    # 2. Link Dependencies
    linked_count = 0
    for t in request.tasks:
        # ScheduledTask has 'dependencies' field (strings of names)
        # In our scheduler logic, these are PREDECESSORS.
        suc_gid = manager.task_registry.get(t.name)
        if not suc_gid: continue
        
        for pred_name in t.dependencies:
            pred_gid = manager.task_registry.get(pred_name)
            if pred_gid:
                manager.link_dependency(suc_gid, pred_gid)
                linked_count += 1
                time.sleep(0.3) # Prevent Rate Limiting
                
    # 3. Handle Sections
    print("Handling Sections...")
    gid_map = manager.task_registry # Name -> GID
    
    try:
        for task in request.tasks:
                # Debug Print
                try:
                    print(f"Task: {task.name} | Section: {task.section} | GID found: {task.name in gid_map}")
                except: 
                    pass # Ignore print errors
                
                if task.section and task.name in gid_map:
                    try:
                        sec_gid = manager.get_or_create_section(task.section)
                        if sec_gid:
                            manager.move_task_to_section(gid_map[task.name], sec_gid)
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
