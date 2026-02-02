import asana
from asana.rest import ApiException
from datetime import datetime, timedelta
import collections
from typing import List, Dict, Optional
from .models import ScheduledTask, AsanaConfig

def add_business_days(from_date: datetime, days_to_add: int) -> datetime:
    current = from_date
    while days_to_add > 0:
        current += timedelta(days=1)
        if current.weekday() < 5: # Mon-Fri
            days_to_add -= 1
    return current

class Scheduler:
    def __init__(self):
        self.tasks = {}  # ID -> TaskData
        self.tasks_by_name = collections.defaultdict(list) # Name -> [List of IDs]
        self.adjacency = collections.defaultdict(list)  # Pred_ID -> [(Succ_ID, Lag)]
        self.reverse_adjacency = collections.defaultdict(list)  # Succ_ID -> [(Pred_ID, Lag)]

    def add_task(self, task_id, name, section=None):
        if task_id not in self.tasks:
            self.tasks[task_id] = {
                'id': task_id,
                'name': name,
                'start_date': datetime.now(),
                'duration': 0,
                'end_date': datetime.now(),
                'gid': None,
                'section': section
            }
            self.tasks_by_name[name].append(task_id)

    def resolve_successor(self, predecessor_id, successor_name, lag_days):
        predecessor = self.tasks.get(predecessor_id)
        if not predecessor: return
        
        # 1. Try Exact Match
        candidates = self.tasks_by_name.get(successor_name, [])
        
        # 2. Try Case-Insensitive Match if needed (Optional, but safe)
        if not candidates:
            # Simplistic linear search (could be optimized with a secondary map)
            successor_name_lower = successor_name.lower()
            for name, ids in self.tasks_by_name.items():
                if name.lower() == successor_name_lower:
                    candidates.extend(ids)
                    break
        
        selected_succ_id = None
        pred_section = predecessor.get('section')

        if candidates:
            # 1. Context-Aware: Check Same Section
            if pred_section:
                for cand_id in candidates:
                    if self.tasks[cand_id].get('section') == pred_section:
                        selected_succ_id = cand_id
                        break
            
            # 2. Global Fallback: Pick first available
            if not selected_succ_id:
                selected_succ_id = candidates[0]
        
        # 3. Auto-Create if Not Found (Leaf Task)
        if not selected_succ_id:
            # Generate ID based on Name + Section (to support duplicates across sections)
            # Sanitize name for ID
            safe_name = "".join(c for c in successor_name if c.isalnum())
            safe_sec = "".join(c for c in (pred_section or "general") if c.isalnum())
            new_id = f"auto_{safe_name}_{safe_sec}"[:50] # Limit length
            
            # Create it
            self.add_task(new_id, successor_name, section=pred_section)
            # print(f"Auto-Created Leaf Task: {successor_name} (ID: {new_id}) in {pred_section}")
            selected_succ_id = new_id
            
        # Add dependency: Predecessor -> Successor
        self.adjacency[predecessor_id].append((selected_succ_id, lag_days))
        self.reverse_adjacency[selected_succ_id].append((predecessor_id, lag_days))
        
        return selected_succ_id

    def inherit_missing_sections(self):
        # Propagate sections to orphan tasks (tasks with no section)
        for t_id, task in self.tasks.items():
            if not task.get('section'):
                # Check Predecessors (Row Tasks that point to this one)
                preds = self.reverse_adjacency[t_id]
                for pred_id, _ in preds:
                    pred_sec = self.tasks.get(pred_id, {}).get('section')
                    if pred_sec:
                        task['section'] = pred_sec
                        # print(f"Inherited Section '{task['section']}' for Orphan '{task['name']}'")
                        break

    def calculate_dates(self):
        # 1. Start all at Today
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        for t in self.tasks.values():
            t['start_date'] = today
            # Workday Logic:
            # Duration 1 -> Inclusive End = Start + (1-1) = Start (if start is workday)
            # Duration N -> End = Start + (N-1) workdays
            
            # Snap start to workday if needed (though tentative start is today)
            while t['start_date'].weekday() >= 5:
                 t['start_date'] += timedelta(days=1)
                 
            d = max(1, t['duration'])
            t['end_date'] = add_business_days(t['start_date'], d - 1)

        # 2. Relax edges
        changed = True
        iterations = 0
        max_iter = len(self.tasks) + 10

        while changed and iterations < max_iter:
            changed = False
            iterations += 1
            for task_name in self.tasks:
                task = self.tasks[task_name]
                preds = self.reverse_adjacency[task_name]
                if not preds: continue

                max_pred_finish = today
                for pred_name, lag in preds:
                    if pred_name in self.tasks:
                        pred_end = self.tasks[pred_name]['end_date']
                        candidate_start = pred_end + timedelta(days=lag)
                        if candidate_start > max_pred_finish:
                            max_pred_finish = candidate_start

                if max_pred_finish > task['start_date']:
                    task['start_date'] = max_pred_finish
                    task['start_date'] = max_pred_finish
                    
                    # Ensure start is a workday (if dependency finished Friday, Start could be Sat? No, skip to Mon)
                    # "Right After" -> If Pred End = Fri. Next Work Day is Mon? 
                    # OR if we want "Zero Lag", does it mean Same Day? 
                    # User said "count Thurs and Fri as 2 days". Implies Inclusive.
                    # Asana dependencies usually imply Start AFTER Pred Finish.
                    # If Pred Finish Friday. 
                    # Options: Start Friday (Overlap) or Start Monday.
                    # Let's assuming "Sequential" -> Start Monday.
                    
                    # BUT Current Logic was `candidate_start = pred_end + lag`.
                    # If lag=0. Start = Pred End.
                    # If Pred End = Fri. Start = Fri.
                    # Workday Logic: Start Fri.
                    # End = AddBiz(Fri, Dur-1).
                    
                    # Wait, if Pred End is Fri. It means it finishes at end of Friday.
                    # So Successor should start Monday? 
                    # "Start date of dependent task must shift... ensuring started right after"
                    # If I finish work Friday 5pm. Next work starts Monday 9am.
                    # So Start should be Next Workday after Pred End.
                    
                    # Let's modify Candidate Start logic (Line 53) directly? 
                    # No, let's keep it simple first: Start = Max Pred Finish.
                    # If Max Pred Finish is Fri. Start = Fri.
                    # If Duration 1. End = Fri.
                    # This implies they happen on SAME DAY.
                    # If user implies "Sequential", then Start should be Next Workday?
                    # "dependent task start date must be 31st Jan only" (when prev ended 29th + 2 days gap?)
                    # Let's stick to "Start = End" (Same Day Handoff) for 0-lag.
                    # Because user complained about "0 days" task being Same Day Start/End.
                    
                    # Only adjustment: If Start falls on Weekend, move to Monday.
                    while task['start_date'].weekday() >= 5:
                        task['start_date'] += timedelta(days=1)
                        
                    d = max(1, task['duration'])
                    task['end_date'] = add_business_days(task['start_date'], d - 1)
                    changed = True
        return iterations
    
    def get_scheduled_tasks(self) -> List[ScheduledTask]:
        sorted_tasks = sorted(self.tasks.values(), key=lambda x: x['start_date'])
        result = []
        for t in sorted_tasks:
            dep_ids = []
            dep_names = []
            if t['id'] in self.reverse_adjacency:
                for pred_id, _ in self.reverse_adjacency[t['id']]:
                    dep_ids.append(pred_id)
                    dep_names.append(self.tasks[pred_id]['name'])
            
            result.append(ScheduledTask(
                id=t['id'],
                name=t['name'],
                start_date=t['start_date'].strftime("%Y-%m-%d"),
                end_date=t['end_date'].strftime("%Y-%m-%d"),
                duration=t['duration'],
                dependencies=dep_ids,
                dependency_names=dep_names,
                section=t.get('section')
            ))
        return result

class AsanaManager:
    def __init__(self, pat, project_gid):
        self.project_gid = project_gid
        config = asana.Configuration()
        config.access_token = pat
        self.client = asana.ApiClient(config)
        self.client = asana.ApiClient(config)
        self.tasks_api = asana.TasksApi(self.client)
        self.sections_api = asana.SectionsApi(self.client)
        self.task_registry = {}
        self.section_cache = {} # Name -> GID

    def get_or_create_section(self, section_name):
        # Normalize name (strip)
        section_name = section_name.strip()
        if section_name in self.section_cache:
            return self.section_cache[section_name]
        
        print(f"[AsanaManager] Looking for section '{section_name}' in project {self.project_gid}...")
        
        # Check existing sections first
        try:
            # Note: get_sections returns a generator of dictionaries or objects
            sections = self.sections_api.get_sections_for_project(self.project_gid, {'opt_fields': 'name'})
            for s in sections:
                # Handle varying response types (dict vs object)
                s_name = s.get('name') if isinstance(s, dict) else getattr(s, 'name', None)
                s_gid = s.get('gid') if isinstance(s, dict) else getattr(s, 'gid', None)
                
                if s_name == section_name:
                    print(f"[AsanaManager] Found existing section '{section_name}' (VID: {s_gid})")
                    self.section_cache[section_name] = s_gid
                    return s_gid
            
            # Create new if not found
            print(f"[AsanaManager] Creating NEW section '{section_name}'...")
            body = {"data": {"name": section_name}}
            # Hypothesis: Function signature is (project_gid, opts) where opts needs 'body' key
            # or (project_gid, body=...) was not parsing?
            # Let's try passing body as a keyword argument again BUT check if 'opts' wrapper is needed?
            # Actually, let's try (project_gid, {'body': body}) based on KeyError 'body'
            
            # Revert to standard attempt first: check if I can inspect it.
            # But based on KeyError 'body', it expects a dict with 'body' key?
            # Let's try:
            opts = {'body': body}
            res = self.sections_api.create_section_for_project(self.project_gid, opts)
            
            # Helper to extract GID from response
            if hasattr(res, 'data'): res_data = res.data
            elif isinstance(res, dict) and 'data' in res: res_data = res['data']
            else: res_data = res # Fallback
            
            # Handle object vs dict
            gid = res_data.get('gid') if isinstance(res_data, dict) else getattr(res_data, 'gid', None)
            
            if gid:
                print(f"[AsanaManager] Created section '{section_name}' (GID: {gid})")
                self.section_cache[section_name] = gid
                return gid
            else:
                print(f"[AsanaManager] FAILED to extract GID from create_section response: {res}")
                return None
                
        except ApiException as e:
            print(f"[AsanaManager] Error managing section {section_name}: {e}")
            return None

    def move_task_to_section(self, task_gid, section_gid):
        if not section_gid or not task_gid: return
        print(f"[AsanaManager] Moving Task {task_gid} to Section {section_gid}")
        body = {"data": {"task": task_gid}}
        try:
            # Hypothesis: (section_gid, opts) where opts={'body': body}
            opts = {'body': body}
            self.sections_api.add_task_for_section(section_gid, opts)
        except ApiException as e:
             print(f"[AsanaManager] Error moving task {task_gid} to section: {e}")

    def fetch_project_tasks(self):
        tasks_map = {}
        fields_string = "name,start_on,due_on,dependencies,parent"
        opts = {'opt_fields': fields_string}
        try:
            all_tasks = self.tasks_api.get_tasks_for_project(self.project_gid, opts)
            for t in all_tasks:
                if not isinstance(t, dict): t = t.to_dict()
                if t.get('name') == 'Task': continue
                parsed = self._parse_task(t)
                tasks_map[parsed['gid']] = parsed
            return list(tasks_map.values())
        except ApiException as e:
            raise Exception(f"Fetch Failed: {e.reason}")

    def _parse_task(self, api_task):
        deps = [d['gid'] for d in api_task.get('dependencies', [])]
        due_str = api_task.get('due_on')
        start_str = api_task.get('start_on')
        now_str = datetime.now().strftime("%Y-%m-%d")
        if not due_str: due_str = now_str
        if not start_str: start_str = due_str or now_str
        return {
            'gid': api_task.get('gid'), 'name': api_task.get('name', 'Unnamed'),
            'start_on': start_str, 'due_on': due_str, 'dependencies': deps
        }

    def create_task_with_dates(self, name, start_dt_str, due_dt_str):
        body = {
            "data": {
                "name": name, "projects": [self.project_gid],
                "start_on": start_dt_str,
                "due_on": due_dt_str
            }
        }
        try:
            result = self.tasks_api.create_task(body, {})
            if not isinstance(result, dict): result = result.to_dict()
            gid = result['data']['gid'] if 'data' in result else result['gid']
            return gid
        except ApiException as e:
            print(f"Error creating task {name}: {e}")
            return None

    def link_dependency(self, dependent_gid, predecessor_gid):
        body = {"data": {"dependencies": [predecessor_gid]}}
        try:
            self.tasks_api.add_dependencies_for_task(body, dependent_gid)
        except ApiException as e:
             print(f"Error linking dependency: {e}")

    def update_task_dates(self, task_gid, start_on, due_on):
        body = {
            "data": {
                "start_on": start_on,
                "due_on": due_on
            }
        }
        try:
            self.tasks_api.update_task(body, task_gid, {})
        except ApiException as e:
            raise Exception(f"Failed to update task {task_gid}: {e}")
