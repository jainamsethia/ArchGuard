
from archguard.analysis.deps import DependencyHealthResult, Vulnerability
r = DependencyHealthResult(vulnerable_packages=[Vulnerability('p','1.0','CVE-001', 'desc')] * 3, scanned_packages=100)
r.score = max(0.0, 100.0 - len(r.vulnerable_packages) * 10.0)
assert r.score == 70.0
print('PASS: scoring formula correct')
