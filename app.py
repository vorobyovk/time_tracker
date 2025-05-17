from flask import Flask, render_template, request, flash, redirect, url_for, session
import calendar
from datetime import datetime, date as dt_date
import locale
import mysql.connector
from mysql.connector import errorcode
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps

app = Flask(__name__)
app.secret_key = 'финальный_очень_секретный_ключ_для_проекта_календаря_007_xyz'

# --- НАСТРОЙКИ MYSQL (ЗАМЕНИТЕ НА ВАШИ ДАННЫЕ!) ---
DB_HOST = "localhost"
DB_USER = "python"  # ЗАМЕНИТЕ
DB_PASSWORD = "123QWasd!@"  # ЗАМЕНИТЕ
DB_NAME = "work_hours"

# --- Flask-Login Настройка ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_route' 
login_manager.login_message = "Пожалуйста, войдите в систему для доступа к этой странице."
login_manager.login_message_category = "info"

class User(UserMixin):
    def __init__(self, id, username, password_hash, role='user'):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.role = role

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def is_supervisor(self):
        return self.role == 'supervisor'

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    if not conn: return None
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, username, password_hash, role FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        if user_data:
            return User(id=user_data['id'], username=user_data['username'], 
                        password_hash=user_data['password_hash'], role=user_data['role'])
        return None
    except Exception as e:
        print(f"Error loading user {user_id}: {e}")
        return None
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ---
def _init_db_structure():
    conn_init = None
    cursor_init = None
    try:
        conn_init = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD)
        cursor_init = conn_init.cursor()
        cursor_init.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` DEFAULT CHARACTER SET 'utf8mb4' COLLATE 'utf8mb4_unicode_ci'")
        conn_init.commit()
        cursor_init.execute(f"USE `{DB_NAME}`")

        try:
            cursor_init.execute("ALTER TABLE `users` DROP COLUMN `is_admin`;")
            conn_init.commit()
            print("Attempted to drop old 'is_admin' column from 'users' table.")
        except mysql.connector.Error as alter_err:
            if alter_err.errno == 1091: 
                 print("'is_admin' column not found to drop, that's fine.")
            else:
                 print(f"Notice during 'is_admin' drop (may be harmless): {alter_err}")

        users_table_sql = (
            "CREATE TABLE IF NOT EXISTS `users` ("
            "  `id` INT AUTO_INCREMENT PRIMARY KEY,"
            "  `username` VARCHAR(80) UNIQUE NOT NULL,"
            "  `password_hash` VARCHAR(255) NOT NULL,"
            "  `role` VARCHAR(20) NOT NULL DEFAULT 'user' COMMENT 'user, supervisor, admin'"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        )
        cursor_init.execute(users_table_sql)
        conn_init.commit()
        print("Table 'users' schema checked/updated with 'role'.")

        admin_username = "root"
        admin_password_plain = "123QWasd!@"
        admin_password_hash = generate_password_hash(admin_password_plain)
        
        cursor_init.execute("SELECT id, role FROM users WHERE username = %s", (admin_username,))
        admin_record_data = cursor_init.fetchone()
        
        if not admin_record_data:
            cursor_init.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                                (admin_username, admin_password_hash, 'admin'))
            conn_init.commit()
            print(f"Admin user '{admin_username}' created with role 'admin'.")
        elif admin_record_data[1] != 'admin':
            cursor_init.execute("UPDATE users SET role = %s, password_hash = %s WHERE username = %s", 
                                ('admin', admin_password_hash, admin_username))
            conn_init.commit()
            print(f"Admin user '{admin_username}' role updated to 'admin'.")
        else:
            print(f"Admin user '{admin_username}' already exists with role 'admin'.")

        entries_table_sql = (
            "CREATE TABLE IF NOT EXISTS `entries` ("
            "  `id` INT AUTO_INCREMENT PRIMARY KEY,"
            "  `user_id` INT NOT NULL,"
            "  `entry_date` DATE NOT NULL,"
            "  `work_hours` INT DEFAULT 0,"
            "  `work_minutes` INT DEFAULT 0,"
            "  `description` TEXT,"
            "  FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        )
        cursor_init.execute(entries_table_sql)
        conn_init.commit()
        print("Table 'entries' checked/created.")

    except mysql.connector.Error as err:
        print(f"Error during DB initialization: {err}")
    finally:
        if cursor_init: cursor_init.close()
        if conn_init and conn_init.is_connected(): conn_init.close()

_init_db_structure()

def get_db_connection():
    try:
        conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
        return conn
    except mysql.connector.Error as err:
        print(f"CRITICAL: Failed to connect to database {DB_NAME}. Error: {err}")
        return None

try:
    locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'ru_RU')
    except locale.Error:
        print("Warning: Russian locale for LC_TIME not found.")
        pass

class CalendarWithWorkHours(calendar.LocaleHTMLCalendar):
    def __init__(self, firstweekday=calendar.MONDAY, locale=None, daily_work_data=None, current_calendar_year=None, current_calendar_month=None):
        super().__init__(firstweekday, locale)
        self.daily_work_data = daily_work_data if daily_work_data is not None else {}
        self.current_calendar_year = current_calendar_year
        self.current_calendar_month = current_calendar_month

    def formatday(self, day, weekday):
        if day == 0:
            return '<td class="noday">&nbsp;</td>'
        else:
            day_html_content = str(day)
            work_time_str = self.daily_work_data.get(day)
            
            day_link_class = "day-link"
            if work_time_str:
                day_html_content += f"<br><span class='work-time'>{work_time_str}</span>"
                day_link_class += " has-entries"

            if self.current_calendar_year and self.current_calendar_month:
                day_date_iso = f"{self.current_calendar_year:04}-{self.current_calendar_month:02}-{day:02}"
                view_day_url_params = {
                    'year': self.current_calendar_year,
                    'month': self.current_calendar_month,
                    'view_date': day_date_iso
                }
                try: 
                    if 'total_time' in request.args:
                        view_day_url_params['total_time'] = request.args.get('total_time')
                    # Для супервайзера, если он просматривает чужой календарь, сохраняем target_user_id в ссылках дней
                    if current_user.is_supervisor and 'target_user_id' in request.args and request.args.get('target_user_id'):
                         view_day_url_params['target_user_id'] = request.args.get('target_user_id')
                except RuntimeError: pass
                
                view_day_url = url_for('index', **view_day_url_params)
                day_html_content = f'<a href="{view_day_url}" class="{day_link_class}">{day_html_content}</a>'
            
            return f'<td class="{self.cssclasses[weekday]}">{day_html_content}</td>'

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Доступ к этой странице имеют только администраторы.", "error")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login_route():
    if current_user.is_authenticated:
        if current_user.is_admin: return redirect(url_for('admin_users_list_route'))
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember_me') else False
        conn = get_db_connection()
        if not conn:
            flash("Ошибка подключения к базе данных. Попробуйте позже.", "error")
            return render_template('login.html')
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, username, password_hash, role FROM users WHERE username = %s", (username,))
            user_data = cursor.fetchone()
            cursor.close()
            if user_data and check_password_hash(user_data['password_hash'], password):
                user_obj = User(id=user_data['id'], username=user_data['username'], password_hash=user_data['password_hash'], role=user_data['role'])
                login_user(user_obj, remember=remember)
                flash(f"Добро пожаловать, {user_obj.username}!", "success")
                next_page = request.args.get('next')
                if user_obj.is_admin: return redirect(next_page or url_for('admin_users_list_route'))
                return redirect(next_page or url_for('index'))
            else:
                flash("Неверное имя пользователя или пароль.", "error")
        except mysql.connector.Error as db_err: flash(f"Ошибка базы данных при входе: {db_err}", "error")
        finally:
            if conn.is_connected(): conn.close()
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout_route():
    logout_user()
    flash("Вы успешно вышли из системы.", "info")
    return redirect(url_for('login_route'))

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if current_user.is_admin:
        return redirect(url_for('admin_users_list_route'))

    now = datetime.now()
    current_year, current_month = now.year, now.month

    selected_year = request.args.get('year', current_year, type=int)
    selected_month = request.args.get('month', current_month, type=int)
    total_monthly_time_str_arg = request.args.get('total_time', None)
    view_date_str = request.args.get('view_date', None)
    target_user_id_from_url = request.args.get('target_user_id', None, type=int)

    user_id_for_data_display = current_user.id 
    is_viewing_other_user = False
    target_username_viewed = None
    viewable_users_for_supervisor = []

    if current_user.is_supervisor:
        conn_users = get_db_connection()
        if conn_users:
            cursor_users = conn_users.cursor(dictionary=True)
            try:
                cursor_users.execute("SELECT id, username FROM users WHERE role = 'user' ORDER BY username")
                viewable_users_for_supervisor = cursor_users.fetchall()
            except mysql.connector.Error as e: flash(f"Ошибка загрузки списка пользователей: {e}", "error")
            finally:
                if cursor_users: cursor_users.close()
                if conn_users.is_connected(): conn_users.close()
        
        if target_user_id_from_url is not None:
            can_view_target = any(u['id'] == target_user_id_from_url for u in viewable_users_for_supervisor)
            if can_view_target:
                user_id_for_data_display = target_user_id_from_url
                is_viewing_other_user = True
                for u in viewable_users_for_supervisor:
                    if u['id'] == target_user_id_from_url: target_username_viewed = u['username']; break
            else:
                flash(f"Пользователь (ID: {target_user_id_from_url}) недоступен для просмотра.", "warning")
                user_id_for_data_display = None 
                is_viewing_other_user = False
        else: 
             user_id_for_data_display = None 
             is_viewing_other_user = False
    
    if request.method == 'POST':
        action_year = request.form.get('year', selected_year, type=int) 
        action_month = request.form.get('month', selected_month, type=int)
        
        user_id_for_post_action = current_user.id
        if current_user.is_supervisor:
            target_user_id_from_form_selector_str = request.form.get('target_user_id_selector')
            if target_user_id_from_form_selector_str and target_user_id_from_form_selector_str.isdigit():
                target_user_id_val = int(target_user_id_from_form_selector_str)
                is_valid_target = any(u['id'] == target_user_id_val for u in viewable_users_for_supervisor)
                if is_valid_target: user_id_for_post_action = target_user_id_val
                else: flash("Выбран неверный пользователь в форме.", "warning"); user_id_for_post_action = None
            elif target_user_id_from_url is not None: user_id_for_post_action = target_user_id_from_url
            else: user_id_for_post_action = None

        if 'submit_entry' in request.form:
            if current_user.is_supervisor:
                flash("Супервайзеры не могут добавлять записи времени.", "error")
                redirect_params_err = {'year': action_year, 'month': action_month}
                if user_id_for_post_action: redirect_params_err['target_user_id'] = user_id_for_post_action
                return redirect(url_for('index', **redirect_params_err))

            redirect_year_entry = request.form.get('current_calendar_year_for_redirect', action_year, type=int)
            redirect_month_entry = request.form.get('current_calendar_month_for_redirect', action_month, type=int)
            try:
                entry_date_obj = dt_date.fromisoformat(request.form.get('entry_date'))
                work_hours = int(request.form.get('work_hours', '0'))
                work_minutes = int(request.form.get('work_minutes', '0'))
                description = request.form.get('description', '').strip()
                if not (0 <= work_hours <= 23): raise ValueError("Часы: 0-23.")
                if not (0 <= work_minutes <= 59): raise ValueError("Минуты: 0-59.")
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    query = ("INSERT INTO entries (user_id, entry_date, work_hours, work_minutes, description) VALUES (%s, %s, %s, %s, %s)")
                    cursor.execute(query, (current_user.id, entry_date_obj, work_hours, work_minutes, description))
                    conn.commit(); flash("Запись успешно добавлена!", "success"); cursor.close(); conn.close()
                else: flash("Ошибка подключения к БД.", "error")
                return redirect(url_for('index', year=redirect_year_entry, month=redirect_month_entry, view_date=entry_date_obj.isoformat()))
            except Exception as e: flash(f"Ошибка при добавлении записи: {e}", "error")
            selected_year, selected_month = redirect_year_entry, redirect_month_entry

        elif 'show_calendar' in request.form or 'summarize_month' in request.form:
            total_time_for_redirect = None
            if not user_id_for_post_action and current_user.is_supervisor:
                flash("Пожалуйста, выберите пользователя для выполнения действия.", "warning")
                return redirect(url_for('index', year=action_year, month=action_month)) 
            if 'summarize_month' in request.form:
                conn_sum = get_db_connection()
                if conn_sum:
                    cursor_sum = conn_sum.cursor(dictionary=True)
                    query_sum = ("SELECT SUM(work_hours) as grand_total_hours, SUM(work_minutes) as grand_total_minutes "
                                 "FROM entries WHERE user_id = %s AND MONTH(entry_date) = %s AND YEAR(entry_date) = %s")
                    try:
                        cursor_sum.execute(query_sum, (user_id_for_post_action, action_month, action_year))
                        result = cursor_sum.fetchone()
                        if result and result['grand_total_hours'] is not None:
                            total_h = result['grand_total_hours'] + (result.get('grand_total_minutes',0) // 60)
                            total_m = result.get('grand_total_minutes',0) % 60
                            total_time_for_redirect = f"{total_h}ч {total_m}м"
                        else: total_time_for_redirect = "0ч 0м"
                    except mysql.connector.Error as db_err: flash(f"Ошибка при суммировании: {db_err}", "error")
                    finally:
                        if cursor_sum: cursor_sum.close()
                        if conn_sum.is_connected(): conn_sum.close()
                else: flash("Ошибка подключения к БД для суммирования.", "error")
            
            redirect_params = {'year': action_year, 'month': action_month}
            if current_user.is_supervisor and user_id_for_post_action:
                redirect_params['target_user_id'] = user_id_for_post_action
            if total_time_for_redirect:
                redirect_params['total_time'] = total_time_for_redirect
            return redirect(url_for('index', **redirect_params))

    if not (1 <= selected_month <= 12): selected_month = current_month
    if not (1 <= selected_year <= 9999): selected_year = current_year
    
    html_calendar = "" 
    daily_work_totals = {}
    entries_for_specific_date = []

    if user_id_for_data_display: 
        if view_date_str:
            try:
                selected_specific_date_obj = dt_date.fromisoformat(view_date_str)
                conn_entries_day = get_db_connection()
                if conn_entries_day:
                    cursor_entries_day = conn_entries_day.cursor(dictionary=True)
                    query_entries_day = ("SELECT id, entry_date, work_hours, work_minutes, description "
                                         "FROM entries WHERE user_id = %s AND entry_date = %s ORDER BY id")
                    cursor_entries_day.execute(query_entries_day, (user_id_for_data_display, selected_specific_date_obj))
                    entries_for_specific_date = cursor_entries_day.fetchall()
                    cursor_entries_day.close(); conn_entries_day.close()
            except ValueError: flash("Неверный формат даты.", "error"); view_date_str = None
            except mysql.connector.Error as db_err: flash(f"Ошибка загрузки записей за {view_date_str}: {db_err}", "error")
            
        conn_daily = get_db_connection()
        if conn_daily:
            cursor_daily = conn_daily.cursor(dictionary=True)
            query_agg_daily = ("SELECT DAY(entry_date) as day_of_month, SUM(work_hours) as total_hours, SUM(work_minutes) as total_minutes "
                               "FROM entries WHERE user_id = %s AND MONTH(entry_date) = %s AND YEAR(entry_date) = %s GROUP BY DAY(entry_date)")
            try:
                cursor_daily.execute(query_agg_daily, (user_id_for_data_display, selected_month, selected_year))
                records_daily = cursor_daily.fetchall()
                for record_daily in records_daily:
                    day_val = record_daily['day_of_month']
                    h = record_daily['total_hours'] if record_daily['total_hours'] is not None else 0
                    m = record_daily['total_minutes'] if record_daily['total_minutes'] is not None else 0
                    h += m // 60
                    m = m % 60
                    if h > 0 or m > 0: daily_work_totals[day_val] = f"{h}ч {m}м"
            except mysql.connector.Error as db_err: flash(f"Ошибка загрузки данных календаря: {db_err}", "error")
            finally:
                if cursor_daily: cursor_daily.close()
                if conn_daily.is_connected(): conn_daily.close()
        
        calendar_display_obj = CalendarWithWorkHours(
            firstweekday=calendar.MONDAY, locale=None, 
            daily_work_data=daily_work_totals,
            current_calendar_year=selected_year, current_calendar_month=selected_month
        )
        html_calendar = calendar_display_obj.formatmonth(selected_year, selected_month)
    elif current_user.is_supervisor: 
        if not viewable_users_for_supervisor:
             html_calendar = "<p style='text-align:center; padding: 20px;'>Нет пользователей (с ролью 'user') для просмотра.</p>"
        else:
             html_calendar = "<p style='text-align:center; padding: 20px;'>Пожалуйста, выберите сотрудника из списка выше для просмотра его данных.</p>"

    try:
        dropdown_month_names_list = [(i, calendar.month_name[i]) for i in range(1,13)]
        if not dropdown_month_names_list[0][1]: raise ValueError("Locale month names empty")
    except:
        dropdown_month_names_list = [(1,"Январь"),(2,"Февраль"),(3,"Март"),(4,"Апрель"),(5,"Май"),(6,"Июнь"),(7,"Июль"),(8,"Август"),(9,"Сентябрь"),(10,"Октябрь"),(11,"Ноябрь"),(12,"Декабрь")]
        
    return render_template('index.html',
                           calendar_html=html_calendar, selected_year=selected_year, selected_month=selected_month,
                           dropdown_months=dropdown_month_names_list, today_date_iso=datetime.now().date().isoformat(),
                           total_monthly_time_str=total_monthly_time_str_arg,
                           view_date_str=view_date_str, 
                           entries_for_specific_date=entries_for_specific_date,
                           is_supervisor_viewing_other=is_viewing_other_user,
                           target_username_viewed=target_username_viewed,
                           viewable_users=viewable_users_for_supervisor, 
                           current_target_user_id=target_user_id_from_url if is_viewing_other_user else None
                           )

@app.route('/edit_entry/<int:entry_id>', methods=['GET', 'POST'])
@login_required
def edit_entry_route(entry_id):
    conn = get_db_connection()
    if not conn: flash("Ошибка подключения к БД.", "error"); return redirect(url_for('index'))

    cursor_check = conn.cursor(dictionary=True)
    cursor_check.execute("SELECT id, user_id, entry_date, work_hours, work_minutes, description FROM entries WHERE id = %s", (entry_id,))
    entry_to_edit = cursor_check.fetchone()
    cursor_check.close() 

    if not entry_to_edit:
        flash("Запись не найдена.", "error")
        conn.close(); return redirect(url_for('index'))
    
    if entry_to_edit['user_id'] != current_user.id and not current_user.is_admin:
        flash("У вас нет прав для редактирования этой записи.", "error")
        conn.close()
        return redirect(url_for('index', year=entry_to_edit['entry_date'].year, month=entry_to_edit['entry_date'].month))

    original_year = entry_to_edit['entry_date'].year
    original_month = entry_to_edit['entry_date'].month
    original_view_date = entry_to_edit['entry_date'].isoformat()

    if request.method == 'POST':
        new_entry_date_str = request.form.get('entry_date')
        new_work_hours_str = request.form.get('work_hours', '0')
        new_work_minutes_str = request.form.get('work_minutes', '0')
        new_description = request.form.get('description', '').strip()
        try:
            new_entry_date_obj = dt_date.fromisoformat(new_entry_date_str)
            new_work_hours = int(new_work_hours_str) if new_work_hours_str.isdigit() else 0
            new_work_minutes = int(new_work_minutes_str) if new_work_minutes_str.isdigit() else 0
            if not (0 <= new_work_hours <= 23): raise ValueError("Часы: 0-23.")
            if not (0 <= new_work_minutes <= 59): raise ValueError("Минуты: 0-59.")

            if not conn.is_connected(): conn = get_db_connection()
            if not conn: flash("Ошибка переподключения к БД.", "error"); raise Exception("DB connection lost")

            cursor = conn.cursor()
            if current_user.is_admin and entry_to_edit['user_id'] != current_user.id:
                query_update = ("UPDATE entries SET entry_date = %s, work_hours = %s, work_minutes = %s, description = %s WHERE id = %s")
                cursor.execute(query_update, (new_entry_date_obj, new_work_hours, new_work_minutes, new_description, entry_id))
            else:
                query_update = ("UPDATE entries SET entry_date = %s, work_hours = %s, work_minutes = %s, description = %s WHERE id = %s AND user_id = %s")
                cursor.execute(query_update, (new_entry_date_obj, new_work_hours, new_work_minutes, new_description, entry_id, current_user.id))
            
            conn.commit()
            if cursor.rowcount > 0: flash("Запись успешно обновлена!", "success")
            else: flash("Не удалось обновить запись.", "warning")
            cursor.close(); conn.close()
            return redirect(url_for('index', year=new_entry_date_obj.year, month=new_entry_date_obj.month, view_date=new_entry_date_obj.isoformat()))
        except ValueError as e: flash(f"Ошибка ввода данных: {e}", "error")
        except mysql.connector.Error as db_err: flash(f"Ошибка БД при обновлении: {db_err}", "error")
        except Exception as e: flash(f"Произошла ошибка: {e}", "error")
        if conn and conn.is_connected(): conn.close()
        return render_template('edit_entry.html', entry=entry_to_edit, current_year=original_year, current_month=original_month, current_view_date=original_view_date)

    if conn.is_connected(): conn.close()
    return render_template('edit_entry.html', entry=entry_to_edit, current_year=original_year, current_month=original_month, current_view_date=original_view_date)

@app.route('/delete_entry/<int:entry_id>', methods=['POST'])
@login_required
def delete_entry_route(entry_id):
    conn = get_db_connection()
    if not conn: flash("Ошибка подключения к БД.", "error"); return redirect(url_for('index'))
    entry_year, entry_month, entry_date_for_view = None, None, None
    try:
        cursor_check = conn.cursor(dictionary=True)
        cursor_check.execute("SELECT entry_date, user_id FROM entries WHERE id = %s", (entry_id,))
        entry_data = cursor_check.fetchone()
        cursor_check.close()
        if not entry_data: flash("Запись для удаления не найдена.", "error")
        elif entry_data['user_id'] != current_user.id and not current_user.is_admin:
            flash("У вас нет прав для удаления этой записи.", "error")
            entry_date_obj = entry_data['entry_date']
            entry_year, entry_month, entry_date_for_view = entry_date_obj.year, entry_date_obj.month, entry_date_obj.isoformat()
        else:
            entry_date_obj = entry_data['entry_date']
            entry_year, entry_month, entry_date_for_view = entry_date_obj.year, entry_date_obj.month, entry_date_obj.isoformat()
            cursor_delete = conn.cursor()
            if current_user.is_admin and entry_data['user_id'] != current_user.id:
                 cursor_delete.execute("DELETE FROM entries WHERE id = %s", (entry_id,))
            else:
                cursor_delete.execute("DELETE FROM entries WHERE id = %s AND user_id = %s", (entry_id, current_user.id))
            conn.commit()
            if cursor_delete.rowcount > 0: flash("Запись успешно удалена!", "success")
            else: flash("Не удалось удалить запись.", "warning")
            cursor_delete.close()
    except mysql.connector.Error as db_err: flash(f"Ошибка БД при удалении: {db_err}", "error")
    except Exception as e: flash(f"Ошибка при удалении: {e}", "error")
    finally:
        if conn.is_connected(): conn.close()
    if entry_year and entry_month:
        return redirect(url_for('index', year=entry_year, month=entry_month, view_date=entry_date_for_view))
    return redirect(url_for('index'))

@app.route('/admin/users')
@admin_required
def admin_users_list_route():
    conn = get_db_connection()
    if not conn: flash("Ошибка подключения к БД.", "error"); return redirect(url_for('login_route')) 
    users_list = []
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, username, role FROM users ORDER BY username")
        users_list = cursor.fetchall()
        cursor.close()
    except mysql.connector.Error as db_err: flash(f"Ошибка загрузки списка пользователей: {db_err}", "error")
    finally:
        if conn.is_connected(): conn.close()
    return render_template('admin_users_list.html', users=users_list)

@app.route('/admin/users/add', methods=['GET', 'POST'])
@admin_required
def admin_add_user_route():
    user_roles = ['user', 'supervisor', 'admin'] 
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role', 'user')
        if not username or not password: flash("Имя пользователя и пароль обязательны.", "error")
        elif len(password) < 6: flash("Пароль должен содержать не менее 6 символов.", "error")
        elif role not in user_roles: flash("Выбрана недопустимая роль.", "error")
        else:
            hashed_password = generate_password_hash(password)
            conn = get_db_connection()
            if not conn: flash("Ошибка подключения к БД.", "error")
            else:
                try:
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)", (username, hashed_password, role))
                    conn.commit(); flash(f"Пользователь '{username}' ({role}) успешно добавлен.", "success")
                    cursor.close(); conn.close(); return redirect(url_for('admin_users_list_route'))
                except mysql.connector.Error as err:
                    if err.errno == errorcode.ER_DUP_ENTRY: flash(f"Пользователь '{username}' уже существует.", "error")
                    else: flash(f"Ошибка БД: {err}", "error")
                finally: 
                    if conn and conn.is_connected(): conn.close()
    return render_template('admin_user_form.html', action="Add", user_data={}, user_roles=user_roles, form_title="Добавить нового пользователя")

@app.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_user_route(user_id):
    conn = get_db_connection()
    if not conn: flash("Ошибка подключения к БД.", "error"); return redirect(url_for('admin_users_list_route'))
    user_roles = ['user', 'supervisor', 'admin']
    if request.method == 'POST':
        new_password = request.form.get('password') 
        role = request.form.get('role')
        original_username = request.form.get('original_username') 
        if original_username == 'root' and role != 'admin':
            flash("Роль 'root' не может быть изменена.", "error")
            return redirect(url_for('admin_edit_user_route', user_id=user_id))
        if role not in user_roles: flash("Недопустимая роль.", "error")
        else:
            try:
                cursor = conn.cursor()
                if new_password: 
                    if len(new_password) < 6:
                        flash("Пароль от 6 симв.", "error"); return redirect(url_for('admin_edit_user_route', user_id=user_id))
                    hashed_password = generate_password_hash(new_password)
                    cursor.execute("UPDATE users SET password_hash = %s, role = %s WHERE id = %s", (hashed_password, role, user_id))
                else: 
                    cursor.execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))
                conn.commit(); flash("Данные обновлены.", "success"); cursor.close(); conn.close()
                return redirect(url_for('admin_users_list_route'))
            except mysql.connector.Error as err: flash(f"Ошибка БД: {err}", "error")
            finally:
                if conn and conn.is_connected(): conn.close()
        return redirect(url_for('admin_edit_user_route', user_id=user_id))
    user_data = None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, username, role FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone(); cursor.close()
    except mysql.connector.Error as db_err: flash(f"Ошибка загрузки: {db_err}", "error")
    finally:
        if conn.is_connected(): conn.close()
    if not user_data: flash("Пользователь не найден.", "error"); return redirect(url_for('admin_users_list_route'))
    return render_template('admin_user_form.html', action="Edit", user_data=user_data, user_roles=user_roles, form_title=f"Редактировать: {user_data['username']}")

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user_route(user_id):
    conn = get_db_connection()
    if not conn: flash("Ошибка подключения к БД.", "error"); return redirect(url_for('admin_users_list_route'))
    try:
        cursor = conn.cursor(dictionary=True) 
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user_to_delete = cursor.fetchone()
        if not user_to_delete: flash("Пользователь не найден.", "error")
        elif user_to_delete['username'] == 'root': flash("Нельзя удалить 'root'.", "error")
        elif current_user.id == user_id : flash("Нельзя удалить себя.", "error")
        else:
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit(); flash(f"Пользователь '{user_to_delete['username']}' удален.", "success")
        cursor.close()
    except mysql.connector.Error as err: flash(f"Ошибка БД: {err}", "error")
    finally:
        if conn.is_connected(): conn.close()
    return redirect(url_for('admin_users_list_route'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)