"""Keep model track identities separate from scene IDs; never silently merge conflicts."""


class TrackRegistry:
    def __init__(self, prefix):
        self.prefix = prefix
        self.by_key = {}
        self.tracks = {}

    def assign(self, keys, used):
        if not keys:
            raise ValueError("video observation lacks native track evidence")
        known = {self.by_key[k] for k in keys if k in self.by_key}
        ambiguous = len(known) > 1 or bool(known & used)
        if len(known) == 1 and not ambiguous:
            identifier = next(iter(known))
        else:
            identifier = f"{self.prefix}:t{len(self.tracks) + 1:04d}"
            self.tracks[identifier] = {
                "object_id": identifier,
                "identity_scope": "this video run only",
                "identity_status": "ambiguous" if ambiguous else "model_track_unverified",
                "native_track_keys": [],
                "observations": [],
            }
        if not ambiguous:
            for key in keys:
                self.by_key[key] = identifier
        self.tracks[identifier]["native_track_keys"] = sorted(
            set(self.tracks[identifier]["native_track_keys"]) | set(keys)
        )
        used.add(identifier)
        return identifier, ambiguous
