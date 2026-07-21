class ProfileService:
    def __init__(self):
        self.profiles = {
            1: {"display_name": "Ada"},
            2: {"display_name": "Grace"},
        }

    def read_profile(self, actor_id, target_id):
        if actor_id != target_id:
            raise PermissionError("cannot read another user's profile")
        return dict(self.profiles[target_id])

    def update_profile(self, actor_id, target_id, display_name):
        if actor_id != target_id:
            raise PermissionError("cannot update another user's profile")
        self.profiles[target_id]["display_name"] = display_name
        return dict(self.profiles[target_id])
