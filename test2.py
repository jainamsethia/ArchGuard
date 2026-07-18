import sys
sys.path.insert(0, '.')
from archguard.cli.main import main
try:
    sys.argv = ['archguard', 'analyze', '--repo', '.', '--output', 'json']
    main()
except Exception as e:
    print(e)
