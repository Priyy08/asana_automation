
import json
import os
import sys
from backend.services import AsanaManager

CONFIG_FILE = "polling_config.json"

def debug_sections():
    if not os.path.exists(CONFIG_FILE):
        print("Config file not found.")
        return

    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
        
    pat = config.get("pat")
    project_gid = config.get("project_gid")
    
    if not pat or not project_gid:
        print("credentials missing in config.")
        return
        
    print(f"Testing Asana Section Logic for Project {project_gid}...")
    manager = AsanaManager(pat, project_gid)
    
    # 1. Test Listing Sections
    print("\n--- Listing Sections ---")
    try:
        sections = manager.sections_api.get_sections_for_project(project_gid, {'opt_fields': 'name'})
        count = 0
        test_section_gid = None
        for s in sections:
            count += 1
            s_name = s.get('name') if isinstance(s, dict) else getattr(s, 'name', None)
            s_gid = s.get('gid') if isinstance(s, dict) else getattr(s, 'gid', None)
            print(f" - Found: {s_name} ({s_gid})")
            if s_name == "Preliminary planning":
                test_section_gid = s_gid
                
        print(f"Total Sections: {count}")
    except Exception as e:
        print(f"List Sections Failed: {e}")
        import traceback
        traceback.print_exc()

    # 2. Test Get or Create
    print("\n--- Testing get_or_create_section ('Debug Section') ---")
    try:
        sec_gid = manager.get_or_create_section("Debug Section")
        print(f"Result GID: {sec_gid}")
        
        if sec_gid:
            # 3. Test Move Task (Need a task GID)
            # Fetch one task
            print("\n--- Fetching a task for move test ---")
            tasks = manager.fetch_project_tasks()
            if tasks:
                target_task = tasks[0]
                t_gid = target_task['gid']
                print(f"Attempting to move task '{target_task['name']}' ({t_gid}) into 'Debug Section' ({sec_gid})")
                
                manager.move_task_to_section(t_gid, sec_gid)
            else:
                print("No tasks found to test move.")
                
    except Exception as e:
        print(f"Get/Create/Move Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_sections()
