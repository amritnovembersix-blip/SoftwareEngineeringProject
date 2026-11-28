import { createContext, useContext, useEffect, useState } from "react";
import {
  api,
  clearSession,
  getToken,
  getUser,
  saveSession,
  toSessionUser,
  updateStoredUser,
} from "../api.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => getUser());
  const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(getToken()));

  // On page load, ask the backend who the token belongs to, so a changed role or a
  // disabled account takes effect right away. A 401 logs out (handled in api.js).
  useEffect(() => {
    if (!getToken()) return;
    let active = true;
    api
      .me()
      .then((me) => {
        if (!active) return;
        const fresh = toSessionUser(me);
        updateStoredUser(fresh);
        setUser(fresh);
      })
      .catch(() => {
        /* backend down: keep the stored user; pages show their own errors */
      });
    return () => {
      active = false;
    };
  }, []);

  function login(session, rememberMe = false) {
    saveSession(session, rememberMe);
    setUser(getUser());
    setIsAuthenticated(true);
  }

  function logout() {
    clearSession();
    setUser(null);
    setIsAuthenticated(false);
  }

  const role = (user?.role || "").toLowerCase();
  const value = {
    user,
    role,
    isAdmin: role === "admin",
    isTeacher: role === "teacher",
    isAuthenticated,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}