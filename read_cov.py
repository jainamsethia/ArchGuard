import sys

try:
    with open('final_cov.txt', 'r', encoding='utf-16le', errors='ignore') as f:
        lines = f.readlines()
except:
    with open('final_cov.txt', 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

for line in lines:
    if "archguard" in line or "TOTAL" in line or "Name" in line:
        print(line.strip().replace("\x00", ""))
