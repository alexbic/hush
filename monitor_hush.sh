#!/bin/bash
# monitor_hush.sh — долгосрочный мониторинг HUSH для отлова PortAudio зависаний
# Запуск: ./monitor_hush.sh (фоновый режим) или ./monitor_hush.sh interactive (интерактивный)

set -e

LOG_DIR="/tmp/hush_monitor_logs"
MONITOR_LOG="$LOG_DIR/monitor.log"
ALERT_LOG="$LOG_DIR/alerts.log"
HUSH_PID_FILE="/tmp/hush_monitor.pid"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$MONITOR_LOG"
}

alert_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚨 $1" | tee -a "$ALERT_LOG"
    echo -e "${RED}🚨 АЛЕРТ: $1${NC}"
}

check_hush_process() {
    local hush_pids=$(ps aux | grep -i hush | grep -v grep | awk '{print $2}' | tr '\n' ' ')
    
    if [ -z "$hush_pids" ]; then
        log_message "HUSH не запущен"
        return 1
    fi
    
    local pid_count=$(echo "$hush_pids" | wc -w)
    if [ "$pid_count" -gt 1 ]; then
        alert_message "Найдено несколько процессов HUSH: $hush_pids"
        return 2
    fi
    
    local pid=$(echo "$hush_pids" | tr -d ' ')
    log_message "HUSH работает (PID: $pid)"
    
    # Проверка состояния процесса
    local process_info=$(top -l 1 -pid "$pid" 2>/dev/null | tail -1)
    if echo "$process_info" | grep -q "sleeping"; then
        log_message "Процесс в состоянии sleep"
    elif echo "$process_info" | grep -q "running"; then
        log_message "Процесс активно работает"
    else
        log_message "Неизвестное состояние процесса"
    fi
    
    return 0
}

check_portaudio_errors() {
    if [ ! -f "/private/tmp/vi_debug.log" ]; then
        log_message "Файл лога vi_debug.log не найден"
        return
    fi
    
    # Проверка последних 50 строк на PortAudio ошибки
    local recent_errors=$(tail -n 50 "/private/tmp/vi_debug.log" | grep -i "portaudio\|recorder.start.*FAILED" || true)
    
    if [ -n "$recent_errors" ]; then
        alert_message "Обнаружены PortAudio ошибки:"
        echo "$recent_errors" | tee -a "$ALERT_LOG"
        return 1
    fi
    
    log_message "PortAudio ошибок не обнаружено"
    return 0
}

check_lock_file() {
    if [ -f "/private/tmp/hush.lock" ]; then
        local lock_age=$(($(date +%s) - $(stat -f "%m" "/private/tmp/hush.lock")))
        local lock_owner=$(ls -la "/private/tmp/hush.lock" | awk '{print $3}')
        
        log_message "Файл блокировки существует (возраст: ${lock_age}s, владелец: $lock_owner)"
        
        if [ "$lock_age" -gt 300 ]; then # 5 минут
            alert_message "Файл блокировки старше 5 минут! Возможно зависание."
            return 1
        fi
        
        return 1
    else
        log_message "Файл блокировки отсутствует"
        return 0
    fi
}

analyze_memory_usage() {
    local hush_pids=$(ps aux | grep -i hush | grep -v grep | awk '{print $2}')
    
    for pid in $hush_pids; do
        local mem_info=$(ps -p "$pid" -o pid,ppid,%cpu,%mem,time,command)
        log_message "Использование памяти HUSH $pid: $mem_info"
    done
}

check_disk_space() {
    local disk_usage=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
    
    if [ "$disk_usage" -gt 90 ]; then
        alert_message "Диск почти полный (${disk_usage}% использовано)"
        return 1
    fi
    
    log_message "Диск в норме (${disk_usage}% использовано)"
    return 0
}

generate_daily_report() {
    local report_file="$LOG_DIR/hush_report_$(date +%Y%m%d).txt"
    
    {
        echo "=== HUSH Мониторинг - Дневной отчет $(date) ==="
        echo ""
        echo "=== Процессы HUSH ==="
        ps aux | grep -i hush | grep -v grep || echo "Процессы не найдены"
        echo ""
        echo "=== Файлы блокировки ==="
        ls -la /private/tmp/hush.lock 2>/dev/null || echo "Файлы блокировки не найдены"
        echo ""
        echo "=== Портативные аудио ошибки ==="
        tail -n 100 "/private/tmp/vi_debug.log" | grep -i "portaudio\|recorder.start.*FAILED" || echo "Ошибок не найдено"
        echo ""
        echo "=== Использование памяти ==="
        ps aux | grep -i hush | grep -v grep | awk '{print $11 ": CPU=" $3 "% MEM=" $4 "% TIME=" $9}' || echo "Информация не доступна"
        echo ""
        echo "=== Состояние диска ==="
        df -h / | tail -1
        echo ""
        echo "=== Алерты за день ==="
        if [ -f "$ALERT_LOG" ]; then
            grep "$(date '+%Y-%m-%d')" "$ALERT_LOG" || echo "Алерты за день не найдены"
        else
            echo "Алерты за день не найдены"
        fi
    } > "$report_file"
    
    log_message "Дневной отчет сгенерирован: $report_file"
}

cleanup_old_logs() {
    # Удаляем логи старше 7 дней
    find "$LOG_DIR" -name "monitor.log.*" -mtime +7 -delete 2>/dev/null || true
    find "$LOG_DIR" -name "alerts.log.*" -mtime +7 -delete 2>/dev/null || true
    find "$LOG_DIR" -name "hush_report_*.txt" -mtime +7 -delete 2>/dev/null || true
    
    log_message "Очистка старых логов завершена"
}

# Интерактивный режим
interactive_mode() {
    echo "🔍 Интерактивный режим мониторинга HUSH"
    echo "Нажмите Ctrl+C для остановки"
    echo ""
    
    while true; do
        echo "[$(date '+%H:%M:%S')] === Проверка состояния ==="
        check_hush_process
        check_portaudio_errors
        check_lock_file
        check_disk_space
        analyze_memory_usage
        echo ""
        sleep 10
    done
}

# Фоновый режим
background_mode() {
    local iteration=0
    local daily_report_counter=0
    
    log_message "🚀 Запуск фонового мониторинга HUSH"
    log_message "Логи сохраняются в: $MONITOR_LOG"
    log_message "Алерты сохраняются в: $ALERT_LOG"
    
    while true; do
        iteration=$((iteration + 1))
        daily_report_counter=$((daily_report_counter + 1))
        
        log_message "=== Итерация #$iteration ($(date '+%H:%M:%S')) ==="
        
        # Проверки
        check_hush_process
        check_portaudio_errors
        check_lock_file
        check_disk_space
        analyze_memory_usage
        
        # Ежедневный отчет
        if [ "$daily_report_counter" -ge 288 ]; then # 24 часа * 12 проверок в час
            generate_daily_report
            daily_report_counter=0
        fi
        
        # Ежедневная очистка
        if [ "$iteration" -ge 288 ]; then # 24 часа * 12 проверок в час
            cleanup_old_logs
            iteration=0
        fi
        
        log_message "Ожидание следующей проверки..."
        sleep 300 # Проверка каждые 5 минут
    done
}

# Обработка сигналов для корректного завершения
trap 'log_message "🛑 Мониторинг остановлен"; rm -f "$HUSH_PID_FILE"; exit 0' INT TERM

# Проверка, не запущен ли уже мониторинг
if [ -f "$HUSH_PID_FILE" ]; then
    existing_pid=$(cat "$HUSH_PID_FILE")
    if kill -0 "$existing_pid" 2>/dev/null; then
        echo "Мониторинг уже запущен с PID: $existing_pid"
        echo "Остановите его сначала: kill $existing_pid"
        exit 1
    else
        rm -f "$HUSH_PID_FILE"
    fi
fi

# Создаем директорию для логов
mkdir -p "$LOG_DIR"

# Сохранение PID
echo $$ > "$HUSH_PID_FILE"

# Запуск в зависимости от режима
case "${1:-background}" in
    "interactive")
        interactive_mode
        ;;
    "background")
        background_mode
        ;;
    *)
        echo "Использование: $0 [interactive|background]"
        echo "  interactive - интерактивный режим с выводом в консоль"
        echo "  background  - фоновый режим (по умолчанию)"
        rm -f "$HUSH_PID_FILE"
        exit 1
        ;;
esac