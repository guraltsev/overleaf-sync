"""Overleaf Client"""
##################################################
# MIT License
##################################################
# File: olclient.py
# Description: Overleaf API Wrapper
# Author: Moritz Glöckl
# License: MIT
# Version: 1.2.0
##################################################

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests as reqs
from bs4 import BeautifulSoup

# Where to get the CSRF Token and where to send the login request to
LOGIN_URL = "https://www.overleaf.com/login"
PROJECT_URL = "https://www.overleaf.com/project"    # The dashboard URL
# The URL to download all the files in zip format
DOWNLOAD_URL = "https://www.overleaf.com/project/{}/download/zip"
# UPLOAD_URL = "https://www.overleaf.com/project/{}/upload"  # The URL to upload files
UPLOAD_URL = "https://www.overleaf.com/project/{}/upload"
FOLDER_URL = "https://www.overleaf.com/project/{}/folder"    # The URL to create folders
DELETE_URL = "https://www.overleaf.com/project/{}/{}/{}"    # The URL to delete files
COMPILE_URL = "https://www.overleaf.com/project/{}/compile?enable_pdf_caching=true"    # The URL to compile the project
BASE_URL = "https://www.overleaf.com"    # The Overleaf Base URL
PATH_SEP = "/"    # Use hardcoded path separator for both windows and posix system


def search_dic(name, dic):
    """ Search `name' in dic['docs'] and dic['fileRefs']
    Return file_id and file_type
    """
    for v in dic['docs']:
        if v['name'] == name:
            return v['_id'], 'doc'
    for v in dic['fileRefs']:
        if v['name'] == name:
            return v['_id'], 'file'
    return None, None


class OverleafClient(object):
    """
    Overleaf API Wrapper
    Supports login, querying all projects, querying a specific project, downloading a project and
    uploading a file to a project.
    """

    @staticmethod
    def filter_projects(json_content, more_attrs=None):
        more_attrs = more_attrs or {}
        for p in json_content:
            if not p.get("archived") and not p.get("trashed"):
                if all(p.get(k) == v for k, v in more_attrs.items()):
                    yield p

    def __init__(self, cookie=None, csrf=None, debug_dir=None,
                 cookie_path=None):
        self._cookie = cookie
        self._csrf = csrf
        self._debug_dir = Path(debug_dir) if debug_dir else None
        self._cookie_path = str(Path(cookie_path).resolve()) if cookie_path else None
        self._session = reqs.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0"})
        # GCLB is a short-lived Google load-balancer affinity cookie. Reusing
        # a captured value can pin a fresh session to an invalid backend;
        # Overleaf authentication itself is carried by overleaf_session2.
        session_cookie = (cookie or {}).get("overleaf_session2")
        if session_cookie:
            self._session.cookies.set("overleaf_session2",
                                      session_cookie,
                                      domain=".overleaf.com",
                                      path="/")

    def _debug_log(self, event, **details):
        """Append safe request diagnostics without recording cookie values."""
        if self._debug_dir is None:
            return

        self._debug_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **details,
        }
        with (self._debug_dir / "request-trace.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, sort_keys=True) + "\n")
        if event == "project_dashboard_request":
            print("[debug] Querying the Overleaf project dashboard")
            print("[debug] Cookie file: {}".format(details["cookie_file"]))
            print("[debug] Cookies read: {}".format(
                ", ".join(details["cookie_names"])))
            print("[debug] Cookies sent: {}".format(
                ", ".join(details["sent_cookie_names"])))
            print("[debug] Request URL: {}".format(details["requested_url"]))
        elif event == "project_dashboard_response":
            print("[debug] Final URL: {} (HTTP {})".format(
                details["final_url"], details["status_code"]))
            for redirect in details["redirects"]:
                print("[debug] Redirect: {} --HTTP {}--> {}".format(
                    redirect["url"], redirect["status_code"],
                    redirect["location"] or "(no Location header)"))
        elif event == "project_dashboard_network_error":
            print("[debug] Dashboard request failed: {}".format(
                details["error"]), file=sys.stderr)

    @staticmethod
    def _redirects(response):
        return [{
            "status_code": item.status_code,
            "url": item.url,
            "location": item.headers.get("Location"),
        } for item in response.history]

    def _write_debug_snapshot(self, response):
        """Persist the dashboard response without exposing authentication data."""
        if self._debug_dir is None:
            return

        self._debug_dir.mkdir(parents=True, exist_ok=True)
        html_path = self._debug_dir / "project-dashboard.html"
        metadata_path = self._debug_dir / "project-dashboard.json"
        html_path.write_bytes(response.content)
        metadata_path.write_text(
            json.dumps({
                "cookie_file": self._cookie_path,
                "requested_url": PROJECT_URL,
                "final_url": response.url,
                "status_code": response.status_code,
                "content_type": response.headers.get("Content-Type"),
                "content_length": len(response.content),
                "redirects": self._redirects(response),
            }, indent=2),
            encoding="utf-8")
        print("Debug dashboard snapshot written to {}".format(html_path))

    def _projects_payload(self):
        """Fetch and parse the dashboard project payload.

        The dashboard is an undocumented Overleaf interface, so retain the
        received HTML in debug mode before parsing it. This makes redirects,
        CAPTCHA pages, and markup changes directly inspectable.
        """
        self._debug_log("project_dashboard_request",
                        cookie_file=self._cookie_path,
                        cookie_names=sorted((self._cookie or {}).keys()),
                        sent_cookie_names=sorted(
                            cookie.name for cookie in self._session.cookies),
                        requested_url=PROJECT_URL)
        try:
            projects_page = self._session.get(PROJECT_URL)
        except reqs.RequestException as exc:
            self._debug_log("project_dashboard_network_error",
                            requested_url=PROJECT_URL,
                            error=str(exc))
            raise
        self._write_debug_snapshot(projects_page)
        self._debug_log("project_dashboard_response",
                        requested_url=PROJECT_URL,
                        final_url=projects_page.url,
                        status_code=projects_page.status_code,
                        redirects=self._redirects(projects_page))
        projects_page.raise_for_status()

        page = BeautifulSoup(projects_page.content, 'html.parser')
        project_blob = page.find('meta', {'name': 'ol-prefetchedProjectsBlob'})
        if project_blob is None or not project_blob.get('content'):
            title = page.title.get_text(strip=True) if page.title else "(no title)"
            raise RuntimeError(
                "Overleaf returned a dashboard page without the project list "
                "(HTTP {}, title: {}).{}".format(
                    projects_page.status_code, title,
                    " Inspect {}.".format(
                        self._debug_dir / "project-dashboard.html")
                    if self._debug_dir else " Re-run with --debug to save it."))

        try:
            payload = json.loads(project_blob['content'])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Overleaf returned an unreadable project list.{}".format(
                    " Inspect {}.".format(
                        self._debug_dir / "project-dashboard.html")
                    if self._debug_dir else " Re-run with --debug to save it.")) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get('projects'), list):
            raise RuntimeError("Overleaf returned a project list in an unexpected format.")
        return payload

    def all_projects(self):
        """
        Get all of a user's active projects (= not archived and not trashed)
        Returns: List of project objects
        """
        return list(OverleafClient.filter_projects(
            self._projects_payload()['projects']))

    def get_project(self, project_name):
        """
        Get a specific project by project_name
        Params: project_name, the name of the project
        Returns: project object
        """

        return next(
            OverleafClient.filter_projects(self._projects_payload()['projects'],
                                           {"name": project_name}), None)

    def download_project(self, project_id):
        """
        Download project in zip format
        Params: project_id, the id of the project
        Returns: bytes string (zip file)
        """
        r = self._session.get(DOWNLOAD_URL.format(project_id), stream=True)
        return r.content

    def create_folder(self, project_id, parent_folder_id, folder_name):
        """
        Create a new folder in a project

        Params:
        project_id: the id of the project
        parent_folder_id: the id of the parent folder, root is the project_id
        folder_name: how the folder will be named

        Returns: folder id or None
        """

        params = {"parent_folder_id": parent_folder_id, "name": folder_name}
        headers = {"X-Csrf-Token": self._csrf}
        r = self._session.post(FOLDER_URL.format(project_id),
                               headers=headers,
                               json=params)

        if r.ok:
            return json.loads(r.content)
        elif r.status_code == str(400):
            # Folder already exists
            return
        else:
            raise reqs.HTTPError()

    def get_project_infos(self, project_id):
        """
        Get detailed project infos about the project

        Params:
        project_id: the id of the project

        Returns: project details
        """

        # Overleaf Cloud still serves Socket.IO 0.9 for the editor.  The
        # third-party Python socketIO-client package has changed protocols
        # across releases, so use the small, documented-by-the-server polling
        # exchange directly. This avoids both stale GCLB affinity cookies and
        # failed WebSocket upgrades behind proxies.
        if not self._session.cookies.get("overleaf_session2"):
            raise RuntimeError(
                "Overleaf session cookie is missing. Please log in again.")

        socket_url = BASE_URL + "/socket.io/1/"
        handshake = self._session.get(
            socket_url,
            params={'t': int(time.time() * 1000), 'projectId': project_id},
            timeout=15)
        handshake.raise_for_status()
        session_id = handshake.text.split(':', 1)[0]
        if not session_id:
            raise RuntimeError("Overleaf returned an invalid Socket.IO handshake.")

        polling_url = socket_url + "xhr-polling/" + session_id
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                response = self._session.get(
                    polling_url,
                    params={'t': int(time.time() * 1000)},
                    timeout=max(1, deadline - time.monotonic()))
                response.raise_for_status()
                for packet in self._socketio_packets(response.content):
                    if not packet.startswith("5:::"):
                        continue
                    event = json.loads(packet[4:])
                    if event.get("name") != "joinProjectResponse":
                        continue
                    args = event.get("args", [])
                    if args and isinstance(args[0], dict):
                        project = args[0].get("project")
                        if isinstance(project, dict):
                            return project
        finally:
            # Tell the server this short-lived polling connection is done.
            try:
                self._session.post(
                    polling_url,
                    params={'t': int(time.time() * 1000)},
                    data="0::",
                    headers={'Content-Type': 'text/plain'},
                    timeout=5)
            except reqs.RequestException:
                pass

        raise RuntimeError(
            "Timed out while retrieving the Overleaf project file tree.")

    @staticmethod
    def _socketio_packets(payload):
        """Decode Socket.IO 0.9 polling frames into text packets."""
        delimiter = b"\xef\xbf\xbd"
        packets = []
        position = 0
        while position < len(payload):
            if not payload.startswith(delimiter, position):
                packets.append(payload[position:].decode("utf-8"))
                break
            position += len(delimiter)
            length_end = payload.find(delimiter, position)
            if length_end < 0:
                raise RuntimeError("Overleaf returned a malformed Socket.IO frame.")
            try:
                length = int(payload[position:length_end])
            except ValueError as exc:
                raise RuntimeError(
                    "Overleaf returned a malformed Socket.IO frame length.") from exc
            position = length_end + len(delimiter)
            packet = payload[position:position + length]
            if len(packet) != length:
                raise RuntimeError("Overleaf returned a truncated Socket.IO frame.")
            packets.append(packet.decode("utf-8"))
            position += length
        return packets

    def upload_file(self, project_id, project_infos, file_name, file_size, file):
        """
        Upload a file to the project

        Params:
        project_id: the id of the project
        file_name: how the file will be named
        file_size: the size of the file in bytes
        file: the file itself

        Returns: True on success, False on fail
        """

        # Set the folder_id to the id of the root folder
        folder_id = project_infos['rootFolder'][0]['_id']

        only_file_name = file_name

        # The file name contains path separators, check folders
        if PATH_SEP in file_name:
            # Remove last item since this is the file name
            items = file_name.split(PATH_SEP)
            local_folders, only_file_name = items[:-1], items[-1]
            # Set the current remote folder
            current_overleaf_folder = project_infos['rootFolder'][0]['folders']

            for local_folder in local_folders:
                exists_on_remote = False
                for remote_folder in current_overleaf_folder:
                    # Check if the folder exists on remote, continue with the new folder structure
                    if local_folder.lower() == remote_folder['name'].lower():
                        exists_on_remote = True
                        folder_id = remote_folder['_id']
                        current_overleaf_folder = remote_folder['folders']
                        break
                # Create the folder if it doesn't exist
                if not exists_on_remote:
                    new_folder = self.create_folder(project_id, folder_id,
                                                    local_folder)
                    current_overleaf_folder.append(new_folder)
                    folder_id = new_folder['_id']
                    current_overleaf_folder = new_folder['folders']

        # Upload the file to the predefined folder
        params = {'folder_id': folder_id}
        data = {
            "relativePath": "null",
            "name": only_file_name,
        }
        files = {"qqfile": (file_name, file)}
        headers = {
            "X-CSRF-TOKEN": self._csrf,
        }

        # Upload the file to the predefined folder
        r = self._session.post(UPLOAD_URL.format(project_id),
                               headers=headers,
                               params=params,
                               data=data,
                               files=files)

        return r.status_code == str(200) and json.loads(r.content)["success"]

    def delete_file(self, project_id, project_infos, file_name):
        """
        Deletes a project's file

        Params:
        project_id: the id of the project
        file_name: how the file will be named

        Returns: True on success, False on fail
        """

        file_type = file_id = None
        # The file name contains path separators, check folders
        if PATH_SEP in file_name:
            items = file_name.split(PATH_SEP)
            dir_depth = len(items) - 1
            only_file_name = items[-1]
            current_overleaf_folder = project_infos['rootFolder'][0]['folders']
            for i in range(dir_depth):
                success = False
                for remote_folder in current_overleaf_folder:
                    if items[i] == remote_folder['name']:
                        if i != dir_depth - 1:
                            current_overleaf_folder = remote_folder['folders']
                        else:
                            file_id, file_type = search_dic(
                                only_file_name, remote_folder)
                        success = True
                        break
                if not success:
                    print("Local folder {} does not exist in remote!".format(
                        items[i]))
                    return False
        else:    # File is in root folder
            remote_folder = project_infos['rootFolder'][0]
            file_id, file_type = search_dic(file_name, remote_folder)

        # File not found!
        if file_id is None: return False

        headers = {"X-Csrf-Token": self._csrf}

        r = self._session.delete(DELETE_URL.format(project_id, file_type, file_id),
                                 headers=headers)

        return r.status_code == '204'

    def download_pdf(self, project_id):
        """
        Compiles and returns a project's PDF

        Params:
        project_id: the id of the project

        Returns: PDF file name and content on success
        """
        headers = {"X-Csrf-Token": self._csrf}

        body = {
            "check": "silent",
            "draft": False,
            "incrementalCompilesEnabled": True,
            "rootDoc_id": "",
            "stopOnFirstError": False
        }

        r = self._session.post(COMPILE_URL.format(project_id),
                               headers=headers,
                               json=body)

        if not r.ok:
            raise reqs.HTTPError()

        compile_result = json.loads(r.content)

        if compile_result["status"] != "success":
            raise reqs.HTTPError()

        pdf_file = next(v for v in compile_result['outputFiles']
                        if v['type'] == 'pdf')

        download_req = self._session.get(BASE_URL + pdf_file['url'],
                                         headers=headers)

        if download_req.ok:
            return pdf_file['path'], download_req.content

        return None
