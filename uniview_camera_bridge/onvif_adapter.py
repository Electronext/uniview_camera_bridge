from __future__ import annotations

from onvif_camera import ONVIFCamera


def patch_uniview_camera(uniview_class) -> None:
    """Route the bridge's existing ONVIF PTZ API through the shared client.

    Uniview LAPI, NVR-forwarded imaging, alarm handling and all public bridge
    method names remain unchanged. This is intentionally a narrow runtime
    adapter so the extraction cannot disturb proven vendor-specific paths.
    """
    if getattr(uniview_class, "_shared_onvif_patched", False):
        return

    original_init = uniview_class.__init__

    def __init__(self, host: str, username: str, password: str, timeout: float = 15.0):
        original_init(self, host, username, password, timeout)
        self.onvif = ONVIFCamera(
            self.base_url,
            username,
            password,
            timeout=timeout,
            rewrite_xaddr_host=True,
            action_in_content_type=True,
        )

    def get_ptz_configuration_options(self, profile=None):
        return self.onvif.get_ptz_configuration_options(profile)

    def get_ptz_node_capabilities(self):
        return self.onvif.get_ptz_node_capabilities()

    def get_zoom(self, profile=None):
        return self.onvif.get_zoom(profile)

    def set_zoom(self, target, profile=None):
        return self.onvif.set_zoom(target, profile)

    def continuous_move(self, pan=0.0, tilt=0.0, zoom=0.0, profile=None):
        return self.onvif.continuous_move(pan=pan, tilt=tilt, zoom=zoom, profile=profile)

    def stop_move(self, profile=None):
        # Preserve the established Uniview bridge wire semantics: both axes are
        # included in Stop even on cameras that only implement one of them.
        return self.onvif.stop_move(profile=profile, pan_tilt=True, zoom=True)

    uniview_class.__init__ = __init__
    uniview_class.get_ptz_configuration_options = get_ptz_configuration_options
    uniview_class.get_ptz_node_capabilities = get_ptz_node_capabilities
    uniview_class.get_zoom = get_zoom
    uniview_class.set_zoom = set_zoom
    uniview_class.continuous_move = continuous_move
    uniview_class.stop_move = stop_move
    uniview_class._shared_onvif_patched = True
