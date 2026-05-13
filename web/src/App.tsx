import { Routes, Route } from "react-router-dom";
import RootLayout from "@/components/layout/RootLayout";
import DigestPage from "@/pages/DigestPage";
import SignalDetailPage from "@/pages/SignalDetailPage";
import ActivityPage from "@/pages/ActivityPage";
import MemoryPage from "@/pages/MemoryPage";
import SettingsPage from "@/pages/SettingsPage";
import NotFoundPage from "@/pages/NotFoundPage";

// All routes share the masthead/footer in RootLayout via <Outlet />.
// Keep this file small; routing is the only concern.
export default function App() {
  return (
    <Routes>
      <Route element={<RootLayout />}>
        <Route index element={<DigestPage />} />
        <Route path="signal/:id" element={<SignalDetailPage />} />
        <Route path="signals/:id" element={<SignalDetailPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="memory" element={<MemoryPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
