# إعداد المزامنة مع GitHub

## 1) مرة واحدة: المستودع والتوكن
1. على GitHub اعمل **New repository** باسم مثلاً `clipmaster-data`، واختار **Private**، وفعّل **Add a README file**.
2. من Settings ← Developer settings ← Personal access tokens ← **Fine-grained tokens** ← Generate new token:
   - Repository access: **Only select repositories** ← اختار المستودع ده بس.
   - Permissions ← Repository permissions ← **Contents: Read and write**.
   - انسخ التوكن (بيظهر مرة واحدة). حدّد مدة صلاحية وافتكر تجددّه قبل ما ينتهي.

## 2) على كل جهاز
1. `pip install -r requirements.txt` (مهم `keyring` علشان التوكن يتخزن في مخزن كلمات السر بتاع ويندوز).
2. شغّل البرنامج ← أيقونة الترس ⚙ ← **المزامنة مع GitHub**:
   - اسم المستودع: `username/clipmaster-data`
   - التوكن: الصقه ← **حفظ واختبار الاتصال**.
3. البرنامج بيرفض أي مستودع Public.

## 3) الاستخدام
- المزامنة تلقائية: عند الفتح، وبعد كل تعديل بثواني، كل 5 دقايق، وعند الإغلاق.
- زر ⟳ في أسفل القائمة الجانبية = مزامنة الآن، والحالة بتظهر تحته.
- لو نفس الرسالة اتعدلت على الجهازين، **آخر تعديل بيكسب** (اتأكد إن ساعة الجهازين مظبوطة على الإنترنت).
- الاختصارات بتتشارك بين الأجهزة. لو اختصار اتكرر بعد الدمج، الأحدث بيحتفظ بيه.
- قبل أول تشغيل: انسخ `~/.config/ebo-clipboard/data.db` احتياطي (قاعدة البيانات بتتحدث لشكل جديد).
