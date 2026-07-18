import unittest
from unittest.mock import patch
from archguard.dashboard.routes import jobs
try:
    with patch('archguard.dashboard.routes.jobs.check_repo_exists') as mock:
        mock.side_effect = jobs.GitHubRateLimitError('rate limited')
except AttributeError as e:
    print(f'Verification failed: {e}')

