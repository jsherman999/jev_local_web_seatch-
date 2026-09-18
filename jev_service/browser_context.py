"""Fresh cookie/storage contexts for comparison runs; upstream remains untouched."""

import time


def install():
    from browser_harness.admin import ensure_daemon
    from browser_harness.helpers import cdp
    from jev_ultrafast import agent
    from jev_ultrafast.browser import Browser

    class IsolatedBrowser(Browser):
        def __init__(self, url):
            ensure_daemon()
            self.target = None
            self.context = cdp("Target.createBrowserContext", disposeOnDetach=True)["browserContextId"]
            try:
                self.target = cdp("Target.createTarget", url="about:blank", background=True,
                                  browserContextId=self.context)["targetId"]
                self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
                self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780,
                          deviceScaleFactor=1, mobile=False)
                self.call("Emulation.setFocusEmulationEnabled", enabled=True)
                self.call("Page.navigate", url=url)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if self.evaluate("document.readyState") == "complete":
                        break
                    time.sleep(0.02)
            except BaseException:
                self.close()
                raise

        def close(self):
            try:
                super().close()
            finally:
                if self.context:
                    cdp("Target.disposeBrowserContext", browserContextId=self.context)
                    self.context = None

    agent.Browser = IsolatedBrowser
