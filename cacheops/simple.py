# -*- coding: utf-8 -*-
try:
    import cPickle as pickle
except ImportError:
    import pickle
from functools import wraps
import os, time

from django.utils.hashcompat import md5_constructor
from django.conf import settings

from cacheops.conf import redis_conn


__all__ = ('cache', 'cached', 'file_cache')


class CacheMiss(Exception):
    pass


class BaseCache(object):
    """
    Simple cache with time-based invalidation
    """
    def cached(self, cache_key=None, timeout=None):
        """
        A decorator for caching function calls
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                # Calculating cache key based on func module, name and arguments
                _cache_key = cache_key
                if _cache_key is None:
                    parts = (func.__module__, func.__name__, repr(args), repr(kwargs))
                    _cache_key = '%s' % md5_constructor('.'.join(parts)).hexdigest()
                _cache_key = 'c:%s' % _cache_key

                try:
                    result = self.get(_cache_key)
                except CacheMiss:
                    result = func(*args, **kwargs)
                    self.set(_cache_key, result, timeout)

                return result
            return wrapper
        return decorator


class RedisCache(BaseCache):
    def __init__(self, conn):
        self.conn = conn

    def get(self, cache_key):
        data = self.conn.get(cache_key)
        if data is None:
            raise CacheMiss
        return pickle.loads(data)

    def set(self, cache_key, data, timeout=None):
        pickled_data = pickle.dumps(data, -1)
        if timeout is not None:
            self.conn.setex(cache_key, pickled_data, timeout)
        else:
            self.conn.set(cache_key, pickled_data)

cache = RedisCache(redis_conn)
cached = cache.cached


FILE_CACHE_DIR = getattr(settings, 'FILE_CACHE_DIR', '/tmp/file_cache')
FILE_CACHE_TIMEOUT = getattr(settings, 'FILE_CACHE_TIMEOUT', 60*60*24*30)

class FileCache(BaseCache):
    """
    A file cache which fixes bugs and misdesign in django default one.
    Uses mtimes in the future to designate expire time. This makes unnecessary
    reading stale files.
    """
    def __init__(self, path, timeout=FILE_CACHE_TIMEOUT):
        self._dir = path
        self._default_timeout = timeout

    def _key_to_filename(self, key):
        """
        Returns a filename corresponding to cache key
        """
        digest = md5_constructor(key).hexdigest()
        return os.path.join(self._dir, digest[-2:], digest[:-2])

    def get(self, key):
        filename = self._key_to_filename(key)
        try:
            # Remove file if it's stale
            if time.time() >= os.stat(filename).st_mtime:
                self.delete(filename)
                raise CacheMiss

            f = open(filename, 'rb')
            data = pickle.load(f)
            f.close()
            return data
        except (IOError, OSError, EOFError, pickle.PickleError):
            raise CacheMiss

    def set(self, key, data, timeout=None):
        filename = self._key_to_filename(key)
        dirname = os.path.dirname(filename)

        if timeout is None:
            timeout = self._default_timeout

        try:
            if not os.path.exists(dirname):
                os.makedirs(dirname)

            # Use open with exclusive rights to prevent data corruption
            f = os.open(filename, os.O_EXCL | os.O_WRONLY | os.O_CREAT)
            try:
                os.write(f, pickle.dumps(data, pickle.HIGHEST_PROTOCOL))
            finally:
                os.close(f)

            # Set mtime to expire time
            os.utime(filename, (0, time.time() + timeout))
        except (IOError, OSError):
            pass

    def delete(self, fname):
        try:
            os.remove(fname)
            # Trying to remove directory in case it's empty
            dirname = os.path.dirname(fname)
            os.rmdir(dirname)
        except (IOError, OSError):
            pass

file_cache = FileCache(FILE_CACHE_DIR)