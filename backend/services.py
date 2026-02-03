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

    def add_task(self, task_id, name, section=None, team=None, responsible=None):
        if task_id not in self.tasks:
            self.tasks[task_id] = {
                'id': task_id,
                'name': name,
                'start_date': datetime.now(),
                'duration': 0,
                'end_date': datetime.now(),
                'gid': None,
                'section': section,
                'team': team,
                'responsible': responsible
            }
            self.tasks_by_name[name].append(task_id)

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
                section=t.get('section'),
                team=t.get('team'),
                responsible=t.get('responsible')
            ))
        return result

    def resolve_successor(self, predecessor_id, successor_name, lag_days):
        predecessor = self.tasks.get(predecessor_id)
        if not predecessor: return
        
        # 1. Try Exact Match
        candidates = self.tasks_by_name.get(successor_name, [])
        
        # 2. Try Case-Insensitive Match if needed
        if not candidates:
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
        
        # 3. Auto-Create if Not Found
        if not selected_succ_id:
            safe_name = "".join(c for c in successor_name if c.isalnum())
            safe_sec = "".join(c for c in (pred_section or "general") if c.isalnum())
            new_id = f"auto_{safe_name}_{safe_sec}"[:50] 
            self.add_task(new_id, successor_name, section=pred_section)
            selected_succ_id = new_id
            
        self.adjacency[predecessor_id].append((selected_succ_id, lag_days))
        self.reverse_adjacency[selected_succ_id].append((predecessor_id, lag_days))
        
        return selected_succ_id

    def inherit_missing_sections(self):
        for t_id, task in self.tasks.items():
            if not task.get('section'):
                preds = self.reverse_adjacency[t_id]
                for pred_id, _ in preds:
                    pred_sec = self.tasks.get(pred_id, {}).get('section')
                    if pred_sec:
                        task['section'] = pred_sec
                        break

    def calculate_dates(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        for t in self.tasks.values():
            t['start_date'] = today
            while t['start_date'].weekday() >= 5:
                 t['start_date'] += timedelta(days=1)
            d = max(1, t['duration'])
            t['end_date'] = add_business_days(t['start_date'], d - 1)

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
                    while task['start_date'].weekday() >= 5:
                        task['start_date'] += timedelta(days=1)
                    d = max(1, task['duration'])
                    task['end_date'] = add_business_days(task['start_date'], d - 1)
                    changed = True
        return iterations

class AsanaManager:
    def __init__(self, pat, project_gid):
        self.project_gid = project_gid
        config = asana.Configuration()
        config.access_token = pat
        self.client = asana.ApiClient(config)
        self.tasks_api = asana.TasksApi(self.client)
        self.sections_api = asana.SectionsApi(self.client)
        self.custom_fields_api = asana.CustomFieldsApi(self.client)
        self.projects_api = asana.ProjectsApi(self.client)
        self.users_api = asana.UsersApi(self.client)
        self.task_registry = {}
        self.section_cache = {} 
        self.custom_field_cache = {}

    def get_workspace_gid(self):
        # Static Workspace GID from User URL: https://app.asana.com/1/1212999353094131/...
        static_gid = "1212999353094131"
        print(f"[AsanaManager] Using Static Workspace GID: {static_gid}")
        return static_gid

    def fetch_workspace_users(self, workspace_gid):
        users_map = {} # Name/Email -> GID
        if not workspace_gid: 
            print("[AsanaManager] No Workspace GID provided to fetch users.")
            return users_map
        
        print(f"[AsanaManager] Fetching users for workspace {workspace_gid}...")
        try:
            # Get all users in workspace
            opts = {'opt_fields': 'name,email,gid', 'workspace': workspace_gid}
            users = self.users_api.get_users(opts)
            # users is a generator or list
            count = 0
            for u in users:
                count += 1
                gid = u.get('gid') if isinstance(u, dict) else getattr(u, 'gid', None)
                name = u.get('name') if isinstance(u, dict) else getattr(u, 'name', None)
                email = u.get('email') if isinstance(u, dict) else getattr(u, 'email', None)
                
                if gid:
                    if name: users_map[name.lower()] = gid
                    if email: users_map[email.lower()] = gid
            
            print(f"[AsanaManager] Successfully fetched {count} users.")
            return users_map
        except ApiException as e:
            print(f"[AsanaManager] Error fetching users: {e}")
            return users_map

    # ... (ensure_text_custom_field matches below, skipped here for brevity if unchanged logic, but replacing block) ...
    # I will keep ensure_text_custom_field as is in the file, just targeting init and adding fetch_users
    # Wait, replace_file_content targets specific lines. 
    # I need to target __init__ (lines 155-166) and insert fetch_workspace_users somewhere.
    # And update create_task_with_dates (lines 291-309).

    def create_task_with_dates(self, name, start_dt_str, due_dt_str, notes="", custom_fields=None, assignee=None):
        data = {
            "name": name, "projects": [self.project_gid],
            "start_on": start_dt_str,
            "due_on": due_dt_str,
            "notes": notes
        }
        if custom_fields:
            data["custom_fields"] = custom_fields
        if assignee:
            data["assignee"] = assignee

        body = {"data": data}
        try:
            result = self.tasks_api.create_task(body, {})
            if not isinstance(result, dict): result = result.to_dict()
            gid = result['data']['gid'] if 'data' in result else result['gid']
            return gid
        except ApiException as e:
            print(f"Error creating task {name}: {e}")
            return None


    def ensure_text_custom_field(self, name, workspace_gid):
        if not workspace_gid: return None
        if name in self.custom_field_cache: return self.custom_field_cache[name]
        
        try:
            settings = self.custom_fields_api.get_custom_field_settings_for_project(self.project_gid, {})
            for s in settings:
                cf = s.get('custom_field') if isinstance(s, dict) else getattr(s, 'custom_field', None)
                if cf:
                    c_name = cf.get('name') if isinstance(cf, dict) else getattr(cf, 'name', None)
                    c_gid = cf.get('gid') if isinstance(cf, dict) else getattr(cf, 'gid', None)
                    if c_name == name:
                        self.custom_field_cache[name] = c_gid
                        return c_gid
        except Exception as e:
            print(f"Error checking project fields: {e}")

        try:
            body = {
                "data": {
                    "name": name,
                    "type": "text",
                    "workspace": workspace_gid,
                    "format": "custom"
                }
            }
            res = self.custom_fields_api.create_custom_field(body, {})
            if isinstance(res, dict): res = res.get('data', res)
            elif hasattr(res, 'data'): res = res.data
            gid = res.get('gid') if isinstance(res, dict) else getattr(res, 'gid', None)
            
            if gid:
                 self.add_custom_field_to_project(gid)
                 self.custom_field_cache[name] = gid
                 return gid
        except ApiException as e:
            print(f"[AsanaManager] Error creating/finding field '{name}': {e}")
            return None

    def add_custom_field_to_project(self, field_gid):
        try:
            body = {"data": {"custom_field": field_gid}}
            self.custom_fields_api.add_custom_field_setting_for_project(self.project_gid, {'body': body})
        except Exception as e:
            print(f"Failed to add field to project: {e}")

    def get_or_create_section(self, section_name):
        section_name = section_name.strip()
        if section_name in self.section_cache:
            return self.section_cache[section_name]
        
        try:
            sections = self.sections_api.get_sections_for_project(self.project_gid, {'opt_fields': 'name'})
            for s in sections:
                s_name = s.get('name') if isinstance(s, dict) else getattr(s, 'name', None)
                s_gid = s.get('gid') if isinstance(s, dict) else getattr(s, 'gid', None)
                if s_name == section_name:
                    self.section_cache[section_name] = s_gid
                    return s_gid
            
            body = {"data": {"name": section_name}}
            opts = {'body': body}
            res = self.sections_api.create_section_for_project(self.project_gid, opts)
            if hasattr(res, 'data'): res_data = res.data
            elif isinstance(res, dict) and 'data' in res: res_data = res['data']
            else: res_data = res
            
            gid = res_data.get('gid') if isinstance(res_data, dict) else getattr(res_data, 'gid', None)
            if gid:
                self.section_cache[section_name] = gid
                return gid
            return None
        except ApiException as e:
            print(f"[AsanaManager] Error managing section {section_name}: {e}")
            return None

    def move_task_to_section(self, task_gid, section_gid):
        if not section_gid or not task_gid: return
        body = {"data": {"task": task_gid}}
        try:
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

    def create_task_with_dates(self, name, start_dt_str, due_dt_str, notes="", custom_fields=None, assignee=None):
        data = {
            "name": name, "projects": [self.project_gid],
            "start_on": start_dt_str,
            "due_on": due_dt_str,
            "notes": notes
        }
        if custom_fields:
            data["custom_fields"] = custom_fields
        if assignee:
            data["assignee"] = assignee

        body = {"data": data}
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
