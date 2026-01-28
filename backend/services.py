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
        self.tasks = {}  # Name -> TaskData
        self.adjacency = collections.defaultdict(list)  # Predecessor -> [(Successor, Lag)]
        self.reverse_adjacency = collections.defaultdict(list)  # Successor -> [(Predecessor, Lag)]

    def add_task(self, name):
        if name not in self.tasks:
            self.tasks[name] = {
                'name': name,
                'start_date': datetime.now(),  # Tentative Start (Today)
                'duration': 0,  # Default to 0 (Same day)
                'end_date': datetime.now(), # Start + 0
                'gid': None
            }

    def add_dependency(self, predecessor_name, successor_name, lag_days):
        self.adjacency[predecessor_name].append((successor_name, lag_days))
        self.reverse_adjacency[successor_name].append((predecessor_name, lag_days))

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
            deps = []
            if t['name'] in self.reverse_adjacency:
                for pred_name, _ in self.reverse_adjacency[t['name']]:
                    deps.append(pred_name)
            
            result.append(ScheduledTask(
                name=t['name'],
                start_date=t['start_date'].strftime("%Y-%m-%d"),
                end_date=t['end_date'].strftime("%Y-%m-%d"),
                duration=t['duration'],
                dependencies=deps
            ))
        return result

class AsanaManager:
    def __init__(self, pat, project_gid):
        self.project_gid = project_gid
        config = asana.Configuration()
        config.access_token = pat
        self.client = asana.ApiClient(config)
        self.tasks_api = asana.TasksApi(self.client)
        self.task_registry = {}

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
