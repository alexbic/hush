#!/bin/bash
# intensive_portaudio_test.sh - интенсивный тест для воспроизведения PortAudio зависаний
# Цель: длительное интенсивное использование записи для выявления зависаний

set -e

APP_PATH="$PWD/HUSH.app"
LOG_DIR="/tmp/intensive_test_logs"
TEST_DURATION=600  # 10 минут тестирования
RECORD_DURATION=5   # 5 секунд записи
PAUSE_DURATION=2    # 2 секунды паузы

echo "=== Интенсивный тест PortAudio для HUSH ==="
echo "Путь к приложению: $APP_PATH"
echo "Директория логов: $LOG_DIR"
echo "Длительность теста: $TEST_DURATION секунд"
echo "Длительность записи: $RECORD_DURATION секунд"
echo "Пауза: $PAUSE_DURATION секунд"
echo ""

# Создаем директорию для логов
mkdir -p "$LOG_DIR"

# Функция для логирования
log_test() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $1" | tee -a "$LOG_DIR/intensive_test.log"
}

# Функция для проверки состояния приложения
check_app_status() {
    if pgrep -f "HUSH.app" > /dev/null; then
        log_test "HUSH запущен (PID: $(pgrep -f HUSH.app))"
        return 0
    else
        log_test "HUSH не запущен, перезапуск..."
        return 1
    fi
}

# Функция для запуска приложения
start_app() {
    log_test "Запуск HUSH..."
    open "$APP_PATH"
    sleep 5  # даем время на запуск
    
    if check_app_status; then
        log_test "HUSH успешно запущен"
        return 0
    else
        log_test "Ошибка: HUSH не запустился"
        return 1
    fi
}

# Функция для симуляции интенсивной записи
intensive_recording() {
    local cycle=$1
    local start_time=$(date +%s)
    
    log_test "Цикл $cycle: Начало интенсивной записи"
    
    # Бесконечный цикл записи с паузами
    while true; do
        current_time=$(date +%s)
        elapsed_time=$((current_time - start_time))
        
        # Проверяем лимит времени
        if [ $elapsed_time -ge $RECORD_DURATION ]; then
            log_test "Цикл $cycle: Достигнут лимит записи ($RECORD_DURATION сек)"
            break
        fi
        
        # Симулируем нажатие Right Option (запись)
        log_test "Цикл $cycle: Запись... ($elapsed_time/$RECORD_DURATION сек)"
        
        # Используем более надежный метод симуляции
        # Вместо osascript используем прямой вызов через AppleScript
        python3 -c "
import subprocess
import time
try:
    # Симулируем нажатие Option
    subprocess.run(['osascript', '-e', 'tell application \"System Events\" to key down {option right}'], timeout=1)
    time.sleep(0.1)
    subprocess.run(['osascript', '-e', 'tell application \"System Events\" to key up {option right}'], timeout=1)
except:
    pass
" > /dev/null 2>&1 || true
        
        sleep 0.5  # короткая пауза между "записями"
    done
    
    log_test "Цикл $cycle: Интенсивная запись завершена"
}

# Основной тест
main() {
    log_test "Начало интенсивного теста"
    log_test "Тест начат: $(date)"
    
    # Запускаем приложение
    if ! start_app; then
        log_test "Критическая ошибка: не удалось запустить приложение"
        exit 1
    fi
    
    # Основной цикл тестирования
    cycle=1
    start_time=$(date +%s)
    max_cycles=20  # 20 циклов по 30 секунд = 10 минут
    
    while [ $cycle -le $max_cycles ]; do
        current_time=$(date +%s)
        elapsed_time=$((current_time - start_time))
        
        if [ $elapsed_time -ge $TEST_DURATION ]; then
            log_test "Достигнут лимит времени теста ($TEST_DURATION сек)"
            break
        fi
        
        log_test "Цикл $cycle/$max_cycles (Прошло: $elapsed_time/$TEST_DURATION сек)"
        
        # Запускаем интенсивную запись
        intensive_recording $cycle
        
        # Короткая пауза между циклами
        sleep $PAUSE_DURATION
        
        cycle=$((cycle + 1))
    done
    
    # Останавливаем приложение
    log_test "Завершение теста..."
    pkill -f "HUSH.app" || true
    sleep 3
    
    if pgrep -f "HUSH.app" > /dev/null; then
        log_test "Принудительное завершение HUSH"
        pkill -9 -f "HUSH.app" || true
        sleep 1
    fi
    
    log_test "Интенсивный тест завершен"
    log_test "Тест завершен: $(date)"
    
    # Анализируем логи
    log_test "Анализ логов..."
    if [ -f "$LOG_DIR/intensive_test.log" ]; then
        echo ""
        echo "=== Анализ интенсивного теста ==="
        
        # Статистика
        total_cycles=$(grep -c "Цикл" "$LOG_DIR/intensive_test.log")
        error_count=$(grep -i "error\|failed\|exception" "$LOG_DIR/intensive_test.log" | wc -l)
        hang_count=$(grep -i "hang\|freeze\|stuck" "$LOG_DIR/intensive_test.log" | wc -l)
        
        echo "Всего циклов: $total_cycles"
        echo "Количество ошибок: $error_count"
        echo "Количество упоминаний зависаний: $hang_count"
        
        # Проверяем логи PortAudio
        if [ -f "/private/tmp/vi_debug.log" ]; then
            portaudio_errors=$(grep -c "PortAudio.*hang" /private/tmp/vi_debug.log 2>/dev/null || echo "0")
            recorder_failures=$(grep -c "recorder.start() FAILED" /private/tmp/vi_debug.log 2>/dev/null || echo "0")
            
            echo "PortAudio зависаний в логах: $portaudio_errors"
            echo "Recorder стартов с ошибкой: $recorder_failures"
        fi
        
        # Показываем последние 15 строк лога
        echo ""
        echo "Последние 15 строк лога:"
        tail -15 "$LOG_DIR/intensive_test.log"
    fi
    
    echo ""
    echo "=== Интенсивный тест завершен ==="
    echo "Логи сохранены в: $LOG_DIR/intensive_test.log"
    echo "Для анализа результатов проверьте директорию: $LOG_DIR"
}

# Запускаем основной тест
main