path=r'C:\Users\user\Desktop\CodePilotAI\android\app\src\main\java\dev\codepilotai\data\api\TokenRefresher.java'
with open(path,'r',encoding='utf-8') as f:
    lines=f.readlines()
for i,l in enumerate(lines[55:68],56):
    print(f'{i}: {l.rstrip()}')
