import json
from pathlib import Path
from archguard.risk.pr_risk import PRRiskAnalyzer
from archguard.analysis.layers import AnalysisOrchestrator
from archguard.fitness.evaluator import FitnessFunctionEvaluator
from archguard.analysis._orchestrator_utils import _get_module_paths

def main():
    repo_root = Path.cwd()
    orchestrator = AnalysisOrchestrator(repo_root)
    
    import subprocess
    result = subprocess.run(["git", "diff", "HEAD~1", "--name-only", "--diff-filter=ACMR"], capture_output=True, text=True)
    py_changed = [repo_root / f for f in result.stdout.strip().splitlines() if f.endswith(".py")]
    
    analyzer = PRRiskAnalyzer()
    module_paths = {m["name"]: _get_module_paths(m) for m in orchestrator.contract.get("modules", [])}
    changed_files_str = [str(f.relative_to(repo_root)).replace("\\", "/") for f in py_changed]
    
    evaluator = FitnessFunctionEvaluator(repo_root, orchestrator.contract)
    dep_set = evaluator._get_module_dependencies()
    dependency_graph = {k: list(v) for k, v in dep_set.items()}
    
    risk_report = analyzer.analyze(
        changed_files=changed_files_str,
        module_paths=module_paths,
        dependency_graph=dependency_graph
    )
    
    print("--- POST-FIX PR RISK AUDIT LOG ---")
    print("1. changed_files count:", len(changed_files_str))
    print("2. module_paths keys/values:", module_paths)
    print("3. final risk_score:", risk_report.risk_score)
    print("4. direct_modules:", [m.module for m in risk_report.module_risks if "Directly modified" in m.reasons[0]])
    print("5. transitive_modules:", [m.module for m in risk_report.module_risks if "Transitively" in m.reasons[0]])

if __name__ == "__main__":
    main()
