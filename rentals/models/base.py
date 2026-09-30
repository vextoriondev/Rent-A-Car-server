import uuid

def uid(): return uuid.uuid4().hex[:16].upper()
