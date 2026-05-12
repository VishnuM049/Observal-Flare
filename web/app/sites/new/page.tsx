import { SiteForm } from "@/components/site-form";

export default function NewSitePage() {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Create Site</h1>
      <div className="card p-6">
        <SiteForm />
      </div>
    </div>
  );
}
