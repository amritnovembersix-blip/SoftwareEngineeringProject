
import { useState } from "react";
import { useNavigate } from "react-router-dom";

function BookIcon() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true">
      <path
        d="M24 12C18 7 9 7 4 10v27c7-3 14-2 20 3 6-5 13-6 20-3V10c-5-3-14-3-20 2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path d="M24 12v28" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function CampusIllustration() {
  return (
    <svg
      className="campus-art"
      viewBox="0 0 520 250"
      role="img"
      aria-label="Illustration of a school campus"
    >
      <g fill="#c9b8df">
        <circle cx="65" cy="177" r="34" />
        <circle cx="94" cy="165" r="42" />
        <circle cx="455" cy="184" r="38" />
        <circle cx="420" cy="170" r="29" />
      </g>
      <g fill="#9d87c5">
        <path d="M75 145 190 115l110 30v88H75Z" />
        <path d="M190 115V78l26-31 26 31v37Z" />
        <path d="M216 47v-15h-5v-9h10v9h-5" />
        <path d="M300 145 365 125l75 20v88H300Z" />
      </g>
      <g fill="#eee8f5">
        <path d="M94 151 190 128l94 23v82H94Z" />
        <path d="M202 116V81l14-18 14 18v35Z" />
        <path d="M318 151 365 137l57 14v82H318Z" />
        <path d="M174 233v-43a17 17 0 0 1 34 0v43Z" />
      </g>
      <g fill="#a38bc8">
        <path d="M110 163h23v27h-23zM150 153h23v27h-23zM230 153h23v27h-23z" />
        <path d="M338 163h18v23h-18zM380 153h18v23h-18z" />
        <path d="M210 82h12v18h-12z" />
      </g>
      <path d="M0 238Q130 214 260 240t260-2v12H0Z" fill="#b9a8d1" />
    </svg>
  );
}

export default function LoginPage() {
  const navigate = useNavigate();

  const [role, setRole] = useState("Teacher");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [message, setMessage] = useState("");

  function handleSubmit(event) {
    event.preventDefault();

    if (!email.trim() || !password.trim()) {
      setMessage("Please enter your email and password.");
      return;
    }

    // Demo authentication only. Real authentication will be connected later.
    const storage = rememberMe ? localStorage : sessionStorage;

    storage.setItem("sassDemoSignedIn", "true");
    storage.setItem("sassDemoRole", role);

    // Remove an old sign-in from the other storage type.
    const otherStorage = rememberMe ? sessionStorage : localStorage;
    otherStorage.removeItem("sassDemoSignedIn");
    otherStorage.removeItem("sassDemoRole");

    navigate("/", { replace: true });
  }

  return (
    <main className="login-page">
      <section className="login-intro">
        <div className="login-brand">
          <BookIcon />
          <span className="login-brand-name">SASS</span>
        </div>

        <p className="login-brand-subtitle">
          Student Academic Support System
        </p>

        <div className="login-accent-line" />

        <div className="login-intro-copy">
          <h1>Academic Support, Simplified</h1>
          <p>
            A secure platform for teachers and administrators to manage
            courses, students, and academic support in one place.
          </p>
        </div>

        <CampusIllustration />
      </section>

      <section className="login-form-side">
        <div className="login-card">
          <div className="login-card-heading">
            <h2>Welcome back</h2>
            <p>Sign in to access your SASS account.</p>
          </div>

          <form onSubmit={handleSubmit}>
            <div className="role-picker" aria-label="Choose your role">
              <button
                type="button"
                className={`role-option ${role === "Teacher" ? "active" : ""}`}
                onClick={() => setRole("Teacher")}
                aria-pressed={role === "Teacher"}
              >
                <span>♙</span> Teacher
              </button>

              <button
                type="button"
                className={`role-option ${role === "Admin" ? "active" : ""}`}
                onClick={() => setRole("Admin")}
                aria-pressed={role === "Admin"}
              >
                <span>♢</span> Admin
              </button>
            </div>

            <label className="login-label" htmlFor="login-email">
              Username or Email
            </label>
            <div className="login-input-wrap">
              <span aria-hidden="true">✉</span>
              <input
                id="login-email"
                type="text"
                placeholder="Enter your email or username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="username"
              />
            </div>

            <label className="login-label" htmlFor="login-password">
              Password
            </label>
            <div className="login-input-wrap">
              <span aria-hidden="true">♙</span>
              <input
                id="login-password"
                type={showPassword ? "text" : "password"}
                placeholder="Enter your password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
              />
              <button
                type="button"
                className="password-toggle"
                onClick={() => setShowPassword(!showPassword)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>

            <div className="login-options">
              <label className="remember-option">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(event) => setRememberMe(event.target.checked)}
                />
                <span>Remember me</span>
              </label>

              <button
                type="button"
                className="login-text-button"
                onClick={() =>
                  setMessage("Please contact your SASS administrator to reset your password.")
                }
              >
                Forgot password?
              </button>
            </div>

            {message && (
              <p className="login-message" role="alert">
                {message}
              </p>
            )}

            <button type="submit" className="login-submit">
              Sign In <span aria-hidden="true">→</span>
            </button>
          </form>

          <div className="login-access-divider">
            <span />
            <p>Need access?</p>
            <span />
          </div>

          <button
            type="button"
            className="request-access-button"
            onClick={() =>
              setMessage("Please contact your SASS administrator to request access.")
            }
          >
            ✉ &nbsp; Request Access
          </button>

          <p className="login-demo-note">
            Demo sign-in: any non-empty email and password will work.
          </p>
        </div>
      </section>
    </main>
  );
}
