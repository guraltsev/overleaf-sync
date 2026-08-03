"""Ol Browser Login Utility"""
##################################################
# MIT License
##################################################
# File: olbrowserlogin.py
# Description: Overleaf Browser Login Utility
# Author: Moritz Glöckl
# License: MIT
# Version: 1.2.0
##################################################

import requests as reqs
from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile,
                                     QWebEngineSettings)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

# Where to get the CSRF Token and where to send the login request to
LOGIN_URL = "https://www.overleaf.com/login"
PROJECT_URL = "https://www.overleaf.com/project"    # The dashboard URL
SOCKET_URL = "https://www.overleaf.com/socket.io/socket.io.js"

# JS snippet to extract the csrfToken
JAVASCRIPT_CSRF_EXTRACTOR = "document.getElementsByName('ol-csrfToken')[0].content"
# Name of the cookies we want to extract
COOKIE_NAMES = ["overleaf_session2", "GCLB"]


class OlBrowserLoginWindow(QMainWindow):
    """
    Overleaf Browser Login Utility
    Opens a browser window to securely login the user and returns relevant login data.
    """

    def __init__(self, *args, **kwargs):
        super(OlBrowserLoginWindow, self).__init__(*args, **kwargs)

        self.webview = QWebEngineView()

        self._cookies = {}
        self._csrf = ""
        self._login_success = False
        self._finishing_login = False

        self.profile = QWebEngineProfile(self.webview)
        self.cookie_store = self.profile.cookieStore()
        self.cookie_store.cookieAdded.connect(self.handle_cookie_added)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.NoPersistentCookies)

        self.profile.settings().setAttribute(QWebEngineSettings.JavascriptEnabled,
                                             True)

        webpage = QWebEnginePage(self.profile, self)
        self.webview.setPage(webpage)
        self.webview.load(QUrl.fromUserInput(LOGIN_URL))
        self.webview.loadFinished.connect(self.handle_load_finished)

        self.setCentralWidget(self.webview)
        self.resize(600, 700)

    def handle_load_finished(self):
        if (self.webview.url().toString() == PROJECT_URL
                and not self._finishing_login):
            self._finishing_login = True
            # The old flow navigated through a dashboard project link that is
            # no longer present. The dashboard itself contains the CSRF token.
            self.webview.page().runJavaScript(JAVASCRIPT_CSRF_EXTRACTOR, 0,
                                              self._finish_login)

    def _finish_login(self, csrf):
        self._csrf = csrf or ""
        # Ensure cookieAdded has delivered the authenticated session before
        # closing the temporary browser profile.
        self.cookie_store.loadAllCookies()

        def quit_after_cookie_delivery():
            self._login_success = bool(
                self._csrf and self._cookies.get("overleaf_session2"))
            QCoreApplication.quit()

        QTimer.singleShot(500, quit_after_cookie_delivery)

    def handle_cookie_added(self, cookie):
        cookie_name = cookie.name().data().decode('utf-8')
        if cookie_name in COOKIE_NAMES:
            self._cookies[cookie_name] = cookie.value().data().decode('utf-8')

    @property
    def cookies(self):
        return self._cookies

    @property
    def csrf(self):
        return self._csrf

    @property
    def login_success(self):
        return self._login_success


def login():
    from PySide6.QtCore import QLoggingCategory
    QLoggingCategory.setFilterRules('qt.webenginecontext.info=false')

    app = QApplication([])
    ol_browser_login_window = OlBrowserLoginWindow()
    ol_browser_login_window.show()
    app.exec()

    if not ol_browser_login_window.login_success:
        return None

    dat = {
        "cookie": ol_browser_login_window.cookies,
        "csrf": ol_browser_login_window.csrf
    }

    # requesting GCLB
    # r = reqs.get(SOCKET_URL, cookies=dat["cookie"])
    # dat["cookie"]['GCLB'] = r.cookies['GCLB']    # type: ignore

    return dat


if __name__ == '__main__':
    login()
