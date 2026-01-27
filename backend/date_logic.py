from datetime import datetime, timedelta
from typing import List, Dict, Set
from .models import ScheduledTask

def recalculate_dates(tasks: List[ScheduledTask], changed_task_gid: str, new_end_date_str: str) -> List[ScheduledTask]:
    """
    Recalculates dates for all tasks based on a change in one task's end date.
    Assumes simple Finish-to-Start dependencies with 0 lag for now, or preserves existing lag logic if we had it.
    For this implementation, we will rebuild the graph and propagate changes.
    """
    
    # 1. Map tasks by GID (or Name if GID not available, but Asana has GIDs)
    # We need to rely on the fact that we are passed current Asana state.
    # In Asana, dependencies are GIDs.
    
    task_map = {t.gid: t for t in tasks if hasattr(t, 'gid') and t.gid} 
    # If tasks coming from Asana don't have 'gid' attribute populated in the list directly? 
    # The Asana fetch returns dicts, but we might be passing Pydantic models.
    # Let's assume input is list of dicts or objects that have 'gid', 'dependencies', 'duration', 'start_date', 'end_date'
    
    # Let's handle generic object access safely
    task_db = {}
    for t in tasks:
        # t might be a ScheduledTask or a dict from Asana
        gid = getattr(t, 'gid', None) or t.get('gid')
        if gid:
            task_db[gid] = t
            
    if changed_task_gid not in task_db:
        return [] # Changed task not found
        
    # 2. Update the changed task
    changed_task = task_db[changed_task_gid]
    
    # Calculate offset
    try:
        old_end = datetime.strptime(getattr(changed_task, 'end_date', None) or changed_task['due_on'], "%Y-%m-%d")
        new_end = datetime.strptime(new_end_date_str, "%Y-%m-%d")
        
        # Update the task
        if isinstance(changed_task, dict):
            changed_task['due_on'] = new_end_date_str
            # Assuming duration stays same, update start? Or keep start and change duration?
            # Requirement: "moving dates... change the end date... dependent tasks must be shifted"
            # This implies the task itself moves, or extends. 
            # Usually if I move end date, I might mean "it takes longer" or "it happens later".
            # Let's assume "It happens later" -> shift entire task.
            duration = (old_end - datetime.strptime(changed_task['start_on'], "%Y-%m-%d")).days
            if duration < 1: duration = 1
            new_start = new_end - timedelta(days=duration)
            changed_task['start_on'] = new_start.strftime("%Y-%m-%d")
        else:
             # Pydantic/Object
             # Note: ScheduledTask uses 'start_date', Asana uses 'start_on'. normalizing...
             pass 

    except Exception as e:
        print(f"Error parsing dates: {e}")
        return []

    # 3. Build Dependency Graph (Successor Map)
    successors = collections.defaultdict(list)
    for gid, t in task_db.items():
        deps = getattr(t, 'dependencies', []) or t.get('dependencies', [])
        for pred_gid in deps:
            successors[pred_gid].append(gid)

    # 4. Propagate (BFS)
    queue = [changed_task_gid]
    visited = set()
    modified_tasks = [changed_task]
    
    while queue:
        current_gid = queue.pop(0)
        if current_gid in visited: continue
        visited.add(current_gid)
        
        current_task = task_db[current_gid]
        current_end_date = datetime.strptime(
            (getattr(current_task, 'end_date', None) or getattr(current_task, 'due_on', None) or current_task.get('due_on')), 
            "%Y-%m-%d"
        )
        
        # Check successors
        for suc_gid in successors[current_gid]:
            suc_task = task_db[suc_gid]
            suc_start_str = getattr(suc_task, 'start_date', None) or getattr(suc_task, 'start_on', None) or suc_task.get('start_on')
            suc_start = datetime.strptime(suc_start_str, "%Y-%m-%d")
            
            # Constraint: Successor Start >= Predecessor End + Lag (0)
            # If Successor Start < Current End (Predecessor Finish), we must push Successor
            # Actually, standard FS is Successor Start >= Predecessor Finish + 1 day? Or same day?
            # Let's assume same day logic for now or +1. Asana usually allows start=due of prev.
            # But let's check: "dependent tasks cannot be started before main task" -> Start >= End ??
            # Let's align with start >= end.
            
            if suc_start < current_end_date:
                # Shift successor
                shift_delta = current_end_date - suc_start
                # Or just distinct set new start = current_end_date
                
                new_suc_start = current_end_date
                
                # Keep duration constant
                suc_end_str = getattr(suc_task, 'end_date', None) or getattr(suc_task, 'due_on', None) or suc_task.get('due_on')
                suc_end = datetime.strptime(suc_end_str, "%Y-%m-%d")
                duration = (suc_end - suc_start).days
                
                new_suc_end = new_suc_start + timedelta(days=duration)
                
                # Update Successor
                if isinstance(suc_task, dict):
                    suc_task['start_on'] = new_suc_start.strftime("%Y-%m-%d")
                    suc_task['due_on'] = new_suc_end.strftime("%Y-%m-%d")
                else:
                    setattr(suc_task, 'start_date', new_suc_start.strftime("%Y-%m-%d"))
                    setattr(suc_task, 'end_date', new_suc_end.strftime("%Y-%m-%d"))

                if suc_task not in modified_tasks:
                   modified_tasks.append(suc_task)
                
                queue.append(suc_gid)
                
    return modified_tasks

def auto_recalibrate(tasks: List[dict]) -> List[dict]:
    """
    Iterates through tasks to find dependency violations and shifts dependent tasks forward.
    Returns the list of tasks that were modified.
    """
    import collections
    
    # 1. Map GID -> Task
    task_map = {t['gid']: t for t in tasks if t.get('gid')}
    
    # 2. Build Adjacency and Indegree for Topological Sort
    graph = collections.defaultdict(list)
    indegree = {gid: 0 for gid in task_map}
    
    for gid, t in task_map.items():
        deps = t.get('dependencies', [])
        for pred_gid in deps:
            if pred_gid in task_map:
                graph[pred_gid].append(gid)
                indegree[gid] += 1
                
    # 3. Topological Sort
    queue = [gid for gid in task_map if indegree[gid] == 0]
    sorted_gids = []
    
    while queue:
        u = queue.pop(0)
        sorted_gids.append(u)
        for v in graph[u]:
            indegree[v] -= 1
            if indegree[v] == 0:
                queue.append(v)
    
    # If cycle exists (graph not fully traversed), we just process what we found 
    # or handle the leftovers. For now, rely on sorted_gids.
    
    modified_tasks = []
    
    # 4. Forward Pass to resolve violations
    # We visit tasks in topological order. For each task, ensure it starts AFTER all preds end.
    
    for gid in sorted_gids:
        task = task_map[gid]
        
        try:
            current_start = datetime.strptime(task['start_on'], "%Y-%m-%d")
            current_due = datetime.strptime(task['due_on'], "%Y-%m-%d")
            duration = (current_due - current_start).days
            if duration < 1: duration = 1 
            
            # Find max predecessor end date
            max_pred_end = None
            
            # task['dependencies'] are Predecessor GIDs
            preds = [task_map[p_gid] for p_gid in task.get('dependencies', []) if p_gid in task_map]
            
            for pred in preds:
                pred_due = datetime.strptime(pred['due_on'], "%Y-%m-%d")
                if max_pred_end is None or pred_due > max_pred_end:
                    max_pred_end = pred_due
            
            # Check Violation or Gap: Start != Max Pred End
            # Constraint: Task should start EXACTLY when ALL preds are done (Zero Lag / "Right After").
            if max_pred_end and current_start != max_pred_end:
                # Needs Shift (Push OR Pull)
                new_start = max_pred_end
                new_due = new_start + timedelta(days=duration)
                
                # Update
                task['start_on'] = new_start.strftime("%Y-%m-%d")
                task['due_on'] = new_due.strftime("%Y-%m-%d")
                
                modified_tasks.append(task)
                
        except (ValueError, TypeError):
            continue 
            
    return modified_tasks

