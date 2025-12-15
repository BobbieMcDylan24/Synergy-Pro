import mysql.connector
from mysql.connector import Error, pooling
from typing import Optional, List, Dict, Any, Tuple
import logging
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MySQLHelper:

    def __init__(self, host: str, database: str, user: str, password: str, port: int = 3306, pool_name: str = "synergy_pro", pool_size: int = 5):
        self.host = host
        self.database = database 
        self.user = user
        self.password = password
        self.port = port
        self.pool_name = pool_name
        self.pool_size = pool_size
        self.connection_pool = None

        self._create_pool()

    def _create_pool(self) -> None:
        try:
            self.connection_pool = pooling.MySQLConnectionPool(pool_name=self.pool_name, pool_size=self.pool_size, pool_reset_session=True, host=self.host, database=self.database, user=self.user, password=self.password, port=self.port, autocommit=False)
            logger.info(f"MySQL connection pool created successfully: {self.pool_name}")
        except Error as e:
            logger.error(f"Error creating connection pool: {e}")
            raise
    
    @contextmanager
    def get_connection(self):
        connection = None
        try:
            connection = self.connection_pool.get_connection()
            yield connection
        except Error as e:
            logger.error(f"Error getting connection from pool: {e}")
            raise
        finally:
            if connection and connection.is_connected():
                connection.close()
    
    def execute_query(self, query: str, params: Optional[tuple] = None, commit: bool = True) -> bool:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params = ())
                if commit:
                    conn.commit()
                cursor.close()
                logger.info(f"Query executed successfully: {query[:50]}...")
                return True
        except Error as e:
            logger.error(f"Error executing query: {e}")
            return False
    
    def fetch_one(self, query: str, params: Optional[Tuple] = None) -> Optional[Tuple]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params or ())
                result = cursor.fetchone()
                cursor.close()
                return result
        except Error as e:
            logger.error(f"Error fetching one: {e}")
            return None

    def fetch_all(self, query: str, params: Optional[Tuple] = None) -> List[Tuple]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params or ())
                results = cursor.fetchall()
                cursor.close()
                return results
        except Error as e:
            logger.error(f"Error fetching all: {e}")
            return []
    
    def fetch_one_dict(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict[str, Any]]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(query, params or ())
                result = cursor.fetchone()
                cursor.close()
                return result
        except Error as e:
            logger.error(f"Error fetching one dict: {e}")
            return None
        
    def fetch_all_dict(self, query: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(query, params or ())
                results = cursor.fetchall()
                cursor.close()
                return results
        except Error as e:
            logger.error(f"Error fetching all dict: {e}")
            return []
    
    def insert(self, table: str, data: Dict[str, Any]) -> Optional[int]:
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(data.values()))
                conn.commit()
                last_id = cursor.lastrowid
                cursor.close()
                logger.info(f"Inserted row into {table} with ID: {last_id}")
                return last_id
        except Error as e:
            logger.error(f"Error inserting into {table}: {e}")
            return None
    
    def update(self, table: str, data: Dict[str, Any], where_clause: str, where_params: Optional[Tuple] = None) -> bool:
        set_clause = ", ".join([f"{key} = %s" for key in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
        params = tuple(data.values()) + (where_params or ())

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                rows_affected = cursor.rowcount
                cursor.close()
                logger.info(f"Updated {rows_affected} rows in {table}")
                return True
        except Error as e:
            logger.error(f"Error updating {table}: {e}")
            return False
        
    def delete(self, table: str, where_clause: str, where_params: Optional[Tuple] = None) -> bool:
        query = f"DELETE FROM {table} WHERE {where_clause}"
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, where_params or ())
                conn.commit()
                rows_affected = cursor.rowcount
                cursor.close()
                logger.info(f"Deleted {rows_affected} rows from {table}")
                return True
        except Error as e:
            logger.error(f"Error deleting from {table}: {e}")
            return False
    
    def table_exists(self, table_name: str) -> bool:
        query = """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
        """
        result = self.fetch_one(query, (self.database, table_name))
        return result[0] > 0 if result else False
    
    def create_table(self, create_query: str) -> bool:
        return self.execute_query(create_query)
    
    def close_pool(self) -> None:
        try:
            if self.connection_pool:
                logger.info("Connection pool cleanup completed")
        except Error as e:
            logger.error(f"Error closing connection pool: {e}")
    
    def get_user_data(self, user_id: int) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM users WHERE user_id = %s"
        return self.fetch_one_dict(query, (user_id,))

    def upsert_user(self, user_id: int, username: str, **additional_data) -> bool:
        data = {
            "user_id": user_id,
            "username": username,
            **additional_data
        }

        columns = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        update_clause = ", ".join([f"{k} = VALUES({k})" for k in data.keys()])

        query = f"""
            INSERT INTO users ({columns})
            VALUES ({placeholders})
            ON DUPLICATE KEY UPDATE {update_clause}
        """

        return self.execute_query(query, tuple(data.values()))