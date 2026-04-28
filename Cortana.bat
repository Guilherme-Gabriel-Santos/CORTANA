@echo off
title Iniciar Cortana

echo Iniciando Frontend da Cortana...
cd /d "c:\Users\guids\Desktop\CORTANA\Layout Cortana\agent-starter-react-main"
start cmd /k "npm run dev"

echo Iniciando Monitoramento SENTINELA OTIMIZADO...
cd /d "c:\Users\guids\Desktop\CORTANA\Aula automacao\Controle_PC"
start cmd /k ""C:\Users\guids\anaconda3\envs\cortana\python.exe" cortana_sentry_optimized.py"

echo Iniciando Backend e Cerebro da Cortana...
cd /d "c:\Users\guids\Desktop\CORTANA\Aula automacao\Controle_PC"
"C:\Users\guids\anaconda3\envs\cortana\python.exe" agent.py dev


pause
