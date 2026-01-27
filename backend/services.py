import asana
from asana.rest import ApiException
from datetime import datetime, timedelta
import collections
from typing import List, Dict, Optional
from .models import ScheduledTask, AsanaConfig

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
            # Delta Math: End = Start + Duration
            t['end_date'] = today + timedelta(days=t['duration'])

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
                    task['end_date'] = task['start_date'] + timedelta(days=task['duration'])
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
