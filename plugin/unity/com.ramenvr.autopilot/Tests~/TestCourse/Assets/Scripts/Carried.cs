// What a game carries from its menu into its levels, as a lobby or a settings manager is: every opening of the menu makes
// one, with nothing to stop a second. A restart has to leave one, as a new launch would (smoke.py, test_carried).

using UnityEngine;

public class Carried : MonoBehaviour
{
    void Awake()
    {
        DontDestroyOnLoad(gameObject);
    }
}
