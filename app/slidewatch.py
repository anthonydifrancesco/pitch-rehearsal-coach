"""Which slide is on screen? Reads PowerPoint through Windows automation (needs PowerPoint + pywin32).
Everywhere else the rehearsal page falls back to manual slide controls."""
from __future__ import annotations

import sys


def current_slide() -> dict:
    if sys.platform != "win32":
        return {"available": False, "reason": "Automatic slide detection only works on Windows with PowerPoint."}
    try:
        import pythoncom
        import win32com.client
    except Exception:
        return {"available": False, "reason": "pywin32 is not installed (run run.bat again to install it)."}
    pythoncom.CoInitialize()  # each server thread needs its own COM initialisation
    try:
        try:
            app = win32com.client.GetActiveObject("PowerPoint.Application")
        except Exception:
            return {"available": False, "reason": "PowerPoint is not open."}
        total = None
        name = ""
        try:
            pres = app.ActivePresentation
            total = int(pres.Slides.Count)
            name = str(pres.Name)
        except Exception:
            pass
        # Slide show running: SlideIndex is the real slide number (CurrentShowPosition skips hidden slides).
        try:
            if app.SlideShowWindows.Count > 0:
                view = app.SlideShowWindows(1).View
                return {"available": True, "slide": int(view.Slide.SlideIndex), "total": total, "name": name,
                        "mode": "slideshow"}
        except Exception:
            pass
        # Editing view (handy for testing without going full screen).
        try:
            return {"available": True, "slide": int(app.ActiveWindow.View.Slide.SlideIndex), "total": total,
                    "name": name, "mode": "editing"}
        except Exception:
            return {"available": False, "reason": "PowerPoint is open but no presentation is showing."}
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
