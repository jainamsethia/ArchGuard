
from archguard.risk.pr_risk import PRRiskAnalyzer, PRRiskReport
import networkx as nx
G = nx.DiGraph()
G.add_edges_from([('api','db'),('service','db'),('api','service')])
analyzer = PRRiskAnalyzer()
report = analyzer.analyze(['api/routes.py'], {'api': ['api/routes.py']}, G)
assert len(report.at_risk_modules) > 0
print(f'PASS: {len(report.at_risk_modules)} at-risk modules detected')
