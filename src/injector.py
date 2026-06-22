import subprocess
import time

def paste(text: str):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.20)  # дать буферу обновиться перед Cmd+V
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to keystroke "v" using {command down}'],
        check=True,
    )

_KEYSTROKE = 'tell application "System Events" to keystroke "v" using {command down}'

def paste_rich(rich_attrs, fmt: str):
    """Помещает NSAttributedString в NSPasteboard, затем симулирует Cmd+V."""
    import AppKit
    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()
    plain = str(rich_attrs.string())
    if fmt == "rtf":
        rtf_data, _ = rich_attrs.RTFFromRange_documentAttributes_(
            AppKit.NSMakeRange(0, rich_attrs.length()), {})
        if rtf_data:
            pb.setData_forType_(rtf_data, AppKit.NSPasteboardTypeRTF)
    elif fmt == "html":
        try:
            html_data, _ = rich_attrs.dataFromRange_documentAttributes_error_(
                AppKit.NSMakeRange(0, rich_attrs.length()),
                {AppKit.NSDocumentTypeDocumentAttribute: AppKit.NSHTMLTextDocumentType},
                None)
            if html_data:
                pb.setData_forType_(html_data, "public.html")
        except Exception:
            pass
    # Всегда включаем plain text как запасной вариант
    pb.setString_forType_(plain, AppKit.NSPasteboardTypeString)
    time.sleep(0.20)
    subprocess.run(["osascript", "-e", _KEYSTROKE], check=True)
