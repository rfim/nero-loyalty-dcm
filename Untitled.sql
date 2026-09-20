DECLARE
    full_name VARCHAR;
    c CURSOR FOR SELECT "database_name", "schema_name", "name" FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));
BEGIN
    SHOW DBT PROJECTS IN ACCOUNT;
    -- To migrate only in a specific database, comment out the line above and uncomment the following:
    -- SHOW DBT PROJECTS IN DATABASE <database_name>;
    OPEN c;
    FOR row_var IN c DO
        full_name := '"' || row_var."database_name" || '"."' || row_var."schema_name" || '"."' || row_var."name" || '"';
        BEGIN
            SELECT SYSTEM$MIGRATE_DBT_PROJECT(:full_name);
            SYSTEM$LOG_INFO('Migrated: ' || :full_name);
        EXCEPTION
            WHEN OTHER THEN
                SYSTEM$LOG_INFO('Failed: ' || :full_name || ' - ' || SQLERRM);
        END;
    END FOR;
    CLOSE c;
    RETURN 'Migration complete.';
END;