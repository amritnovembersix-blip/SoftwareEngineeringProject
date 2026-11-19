
import { createContext, useContext, useState } from "react";
import {
  getToken,
  getUser,
  saveSession,
  clearSession,
} from "../api.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => getUser());
  const [isAuthenticated, setIsAuthenticated] = useState(
    () => Boolean(getToken())
  );

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

  const value = {
    user,
    isAuthenticated,
    login,
    logout,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }

  return context;
}
