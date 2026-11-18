/* =====================================================================================================================
   Логин ТОЛЬКО ДЛЯ ЧТЕНИЯ к базе Logo (LOGO_DB) для приложения «Конструктор бухгалтерских отчётов».

   НЕ ВЫПОЛНЯЕТСЯ автоматически. Запускает администратор SQL Server под логином с правом создавать логины (sysadmin / securityadmin),
   например через SSMS или:  sqlcmd -S <сервер> -E -i docs\logo-readonly-login.sql

   Что делает:
     1. создаёт серверный логин  buh_report_ro  (SQL-аутентификация; пароль задайте ниже, сложный, не из одних цифр);
     2. создаёт в LOGO_DB пользователя с тем же именем;
     3. даёт роль db_datareader (SELECT по всем таблицам и представлениям базы);
     4. явно ЗАПРЕЩАЕТ (DENY) любую запись и изменение схемы - даже если кто-то потом по ошибке добавит логин в другую роль;
     5. серверный доступ ограничен: логин не входит ни в одну серверную роль, VIEW SERVER STATE не выдаётся.

   Приложению нужен только SELECT (оно читает EMFLINE/EMFICHE/EMUHACC/EMCENTER, L_CAPIFIRM/L_CAPIPERIOD и INFORMATION_SCHEMA).
   Приложение само отказывается выполнять что-либо кроме SELECT, но это вторая линия защиты: основная - права этого логина.

   Откат (удаление логина) - в конце файла, закомментирован.
   ===================================================================================================================== */

-- 1. Серверный логин. ЗАМЕНИТЕ пароль на свой (не храните его в репозитории).
USE [master];
GO
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'buh_report_ro')
    CREATE LOGIN [buh_report_ro] WITH PASSWORD = N'ЗАМЕНИТЕ_НА_СЛОЖНЫЙ_ПАРОЛЬ',
        DEFAULT_DATABASE = [LOGO_DB], CHECK_POLICY = ON, CHECK_EXPIRATION = OFF;
GO

-- 2. Пользователь в базе Logo
USE [LOGO_DB];
GO
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'buh_report_ro')
    CREATE USER [buh_report_ro] FOR LOGIN [buh_report_ro];
GO

-- 3. Чтение всех данных
ALTER ROLE [db_datareader] ADD MEMBER [buh_report_ro];
GO

-- 4. Запрет записи и изменения схемы (DENY сильнее любого GRANT / членства в ролях)
-- (в db_datawriter / db_owner этот пользователь входить не должен; если вдруг входил - снимите: ALTER ROLE [db_datawriter] DROP MEMBER [buh_report_ro];)
DENY INSERT, UPDATE, DELETE, ALTER, CONTROL, EXECUTE, REFERENCES, TAKE OWNERSHIP,
     CREATE TABLE, CREATE VIEW, CREATE PROCEDURE, CREATE FUNCTION, CREATE SCHEMA
     TO [buh_report_ro];
GO

-- 5. Проверка (выполнить от имени нового логина: EXECUTE AS USER) - SELECT проходит, запись отклоняется
-- EXECUTE AS USER = N'buh_report_ro';
--     SELECT TOP 1 LOGICALREF FROM LG_001_01_EMFLINE;               -- должно работать
--     UPDATE LG_001_01_EMFLINE SET LINEEXP = LINEEXP WHERE 1 = 0;   -- должно дать: The UPDATE permission was denied
-- REVERT;

/* Откат:
USE [LOGO_DB];  DROP USER IF EXISTS [buh_report_ro];
USE [master];   DROP LOGIN [buh_report_ro];
*/
