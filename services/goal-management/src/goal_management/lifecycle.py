TRANSITIONS={'active':{'pause':'paused','cancel':'cancelled','complete':'completed'},'paused':{'resume':'active','cancel':'cancelled','complete':'completed'},'completed':{},'cancelled':{}}
def transition(current:str,action:str|None)->str|None: return TRANSITIONS.get(current,{}).get(action)
