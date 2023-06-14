import json
import urllib.parse

from . import git_vcs
from ..reporter import ReportObserver, Reporter
from ...lib import utils
from ...lib.gravity import Dependency

__all__ = [
    "GithubActionsMainVcs"
]


class GithubActionsMainVcs(ReportObserver, git_vcs.GitMainVcs):
    """
    This class mostly contains functions for Gihub report observer
    """
    reporter_factory = Dependency(Reporter)

    @staticmethod
    def define_arguments(argument_parser):
        parser = argument_parser.get_or_create_group("GitHub Actions", "GitHub repository settings for GH Actions")

        parser.add_argument("--ghactions-token", "-ght", dest="token", metavar="GITHUB_TOKEN",
                            help="Is stored in ${{ secrets.GITHUB_TOKEN }}")
        parser.add_argument("--ghactions-payload", "-ghp", dest="payload", metavar="GITHUB_PAYLOAD",
                            help="File path: ${{ github.event_path }}")

    def __init__(self, *args, **kwargs):
        self.settings.repo = "Will be filled after payload parsing"
        super().__init__(*args, **kwargs)
        self.reporter = None

        self.check_required_option("token", """
            The GitHub workflow token is not specified.

            For github the git checkout id defines the commit to be checked and reported.
            Please specify the checkout id by using '--git-checkout-id' ('-gco') command
            line parameter or by setting GIT_CHECKOUT_ID environment variable.

            If using 'universum github-handler', the checkout ID is automatically extracted
            from the webhook payload and passed via GIT_CHECKOUT_ID environment variable.
            """)

        self.payload = self.read_and_check_multiline_option("payload", """
            GitHub web-hook payload JSON is not specified.

            Please pass incoming web-hook request payload to this parameter directly via
            '--github-payload' ('-ghp') command line parameter or by setting GITHUB_PAYLOAD
            environment variable, or by passing file path as the argument value (start
            filename with '@' character, e.g. '@/tmp/file.json' or '@payload.json' for
            relative path starting at current directory). Please note, that when passing
            a file, it's expected to be in UTF-8 encoding
            """)

        try:
            self.payload_json = json.loads(self.payload)
            self.settings.repo = self.payload_json['repository']['html_url']
            self.settings.refspec = self.payload_json['pull_request']['head']['ref']
        except json.decoder.JSONDecodeError as error:
            self.error(f"Provided payload value could not been parsed as JSON "
                       f"and returned the following error:\n {error}")

    def _clone(self, history_depth, destination_directory, clone_url):
        parsed_repo = urllib.parse.urlsplit(clone_url)
        if parsed_repo.scheme == "https" and not parsed_repo.username:
            new_netloc = f"{self.settings.token}@{parsed_repo.netloc}"
            parsed_repo = (parsed_repo.scheme, new_netloc, parsed_repo.path, parsed_repo.query, parsed_repo.fragment)
        clone_url = urllib.parse.urlunsplit(parsed_repo)
        super()._clone(history_depth, destination_directory, clone_url)

    def code_review(self):
        self.reporter = self.reporter_factory()
        self.reporter.subscribe(self)
        return self

    def update_review_version(self):
        self.out.log("GitHub has no review versions")

    def get_review_link(self):
        return self.payload_json['pull_request']['html_url']

    def is_latest_version(self):
        return True

    def _report(self, url, request):
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer { self.settings.token }"
        }

        utils.make_request(url, request_method="POST", json=request, headers=headers, timeout=5*60)

    def code_report_to_review(self, report):
        # git show returns string, each file separated by \n,
        # first line consists of commit id and commit comment, so it's skipped
        commit_files = self.repo.git.show("--name-only", "--oneline", self.settings.checkout_id).split('\n')[1:]
        # NB! When using GITHUB_TOKEN, the rate limit is 1,000 requests per hour per repository.
        # (https://docs.github.com/en/rest/overview/resources-in-the-rest-api?apiVersion=2022-11-28  ->
        #                                              #rate-limits-for-requests-from-github-actions)
        for path, issues in report.items():
            if path in commit_files:
                for issue in issues:
                    request = dict(path=path,
                                   commit_id=self.payload_json['pull_request']['head']['sha'],
                                   body=issue['message'],
                                   line=issue['line'],
                                   side="RIGHT")
                    self.out.log(f"request is {request}")
                    self._report(self.payload_json['pull_request']['review_comments_url'], request)

    def report_start(self, report_text):
        pass

    def report_result(self, result, report_text=None, no_vote=False):
        if not report_text:
            report_text = "Universum check finished"
        self._report(self.payload_json['pull_request']['comments_url'], {"body": report_text})

