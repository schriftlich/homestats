// Wird mit esbuild zu public/auth.js gebündelt und als window.Auth bereitgestellt.
import {
  login, logout, getUser, handleAuthCallback, acceptInvite, recoverPassword,
  requestPasswordRecovery, updateUser, onAuthChange,
} from "@netlify/identity";

window.Auth = { login, logout, getUser, handleAuthCallback, acceptInvite, recoverPassword, requestPasswordRecovery, updateUser, onAuthChange };
