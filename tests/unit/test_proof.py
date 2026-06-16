def test_proof_evolution():
    from archguard.evolution.tracker import ArchitectureEvolutionTracker
    import os
    tracker = ArchitectureEvolutionTracker(os.getcwd())
    report = tracker.analyze_history(max_commits=5, max_workers=1)
    print("\n--- EVOLUTION PROOF ---")
    for s in report.snapshots:
        print(f"commit SHA: {s.sha}")
        print(f"health score: {s.health_score}")
        print(f"violation count: {s.violation_count}")
        print(f"composite score: {s.composite_score}")
        print("-" * 15)
    print("-----------------------")
